"""
agent/llm_client.py
====================
Thin wrapper around the LOCAL Ollama HTTP API (default http://localhost:11434).

Why a wrapper?
    1. One place to set model / temperature / seed (taken from the rules file).
    2. Reproducibility: every answer is cached on disk, keyed by a hash of
       (model, options, system prompt, user prompt).  Asking the same question
       twice returns the SAME text even if the GPU/CPU would give a slightly
       different sample - this is what makes "same submission + same rules =
       same grade" true for the LLM-based metrics.
    3. Graceful failure: if Ollama is not running we raise LLMUnavailable and
       every analyzer falls back to a deterministic heuristic.

We use plain `requests` (no ollama pip package, no LangChain) to keep the
dependency list tiny.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import requests

from .common import PROJECT_ROOT, canonical_json, sha256_text

CACHE_DIR = PROJECT_ROOT / ".cache" / "llm"


class LLMUnavailable(Exception):
    """Ollama is disabled, not running, or the model is missing."""


class OllamaClient:
    def __init__(self, cfg: dict):
        self.cfg = dict(cfg)
        self._available: Optional[bool] = None   # lazily checked once

    # ------------------------------------------------------------------ health
    def is_available(self) -> bool:
        """True if the server answers AND the configured model is pulled."""
        if not self.cfg.get("enabled", True):
            return False
        if self._available is None:
            try:
                r = requests.get(self.cfg["host"].rstrip("/") + "/api/tags", timeout=3)
                r.raise_for_status()
                names = [m.get("name", "") for m in r.json().get("models", [])]
                want = self.cfg["model"]
                # "llama3.1" should match "llama3.1:latest"
                self._available = any(n == want or n.split(":")[0] == want.split(":")[0] and ":" not in want
                                      for n in names)
            except Exception:
                self._available = False
        return self._available

    def version(self) -> str:
        """Ollama server version (for the 'tool_versions' block of the report)."""
        try:
            r = requests.get(self.cfg["host"].rstrip("/") + "/api/version", timeout=3)
            return r.json().get("version", "unknown")
        except Exception:
            return "not running"

    # -------------------------------------------------------------------- chat
    def chat(self, system: str, user: str, json_mode: bool = False, **overrides) -> str:
        """
        Send one system+user prompt and return the model's text.

        overrides may replace model / temperature / seed / num_predict for ONE call
        (used when a metric's `params` in the rules file names its own model).
        """
        cfg = {**self.cfg, **{k: v for k, v in overrides.items() if v is not None}}
        if not cfg.get("enabled", True):
            raise LLMUnavailable("LLM disabled in rules (llm.enabled = false)")

        options = {
            "temperature": cfg["temperature"],
            "seed": cfg["seed"],
            "top_k": cfg["top_k"],
            "num_ctx": cfg["num_ctx"],
            "num_predict": cfg["num_predict"],
        }
        payload = {
            "model": cfg["model"],
            "stream": False,
            "options": options,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        if json_mode:
            payload["format"] = "json"      # Ollama forces syntactically valid JSON

        # ---- cache lookup --------------------------------------------------
        key = sha256_text(canonical_json(payload))
        cache_file = CACHE_DIR / f"{key}.json"
        if cfg.get("use_cache", True) and cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))["text"]

        # ---- real call -----------------------------------------------------
        if not self.is_available():
            raise LLMUnavailable(
                f"Ollama model '{cfg['model']}' not reachable at {cfg['host']} "
                f"(run `ollama serve` and `ollama pull {cfg['model']}`)")
        try:
            r = requests.post(cfg["host"].rstrip("/") + "/api/chat", json=payload,
                              timeout=cfg["timeout_s"])
            r.raise_for_status()
            text = r.json()["message"]["content"].strip()
        except Exception as e:
            raise LLMUnavailable(f"Ollama call failed: {e}") from e

        if cfg.get("use_cache", True):
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"text": text}), encoding="utf-8")
        return text


def extract_json(text: str) -> Optional[dict]:
    """
    Pull the first JSON object out of an LLM answer.
    Small local models sometimes wrap JSON in prose or ``` fences; be forgiving.
    """
    text = re.sub(r"```(?:json)?", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    while start != -1:                       # try every '{' until one parses
        depth = 0
        for i in range(start, len(text)):
            depth += (text[i] == "{") - (text[i] == "}")
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    break
        start = text.find("{", start + 1)
    return None
