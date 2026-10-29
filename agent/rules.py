"""
agent/rules.py
===============
Loads and validates the instructor's rules file (JSON).

WHY a separate module?
    The assignment demands that metrics, thresholds and weights live OUTSIDE
    the agent's source code.  This is the only place that reads that file, so
    the rest of the code never touches raw JSON.

Reproducibility:
    We hash the canonical form of the rules (see Rules.hash).  The hash is
    printed in every report so you can prove "same submission + same rules".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .common import PROJECT_ROOT, canonical_json, sha256_text

# Default LLM settings.  Anything in the rules file's top-level "llm" block
# overrides these.  Temperature 0 + fixed seed + top_k 1 = most deterministic
# behaviour Ollama can give; the on-disk cache (llm_client.py) closes the gap.
DEFAULT_LLM = {
    "enabled": True,                    # False -> never call Ollama (heuristic fallbacks)
    "host": "http://localhost:11434",   # where `ollama serve` listens
    "model": "llama3.1:8b",             # any model you have pulled: `ollama pull <name>`
    "temperature": 0.0,
    "seed": 42,
    "top_k": 1,
    "num_ctx": 4096,                    # context window in tokens
    "num_predict": 700,                 # max tokens the model may generate per call
    "timeout_s": 180,                   # HTTP timeout per call
    "use_cache": True,                  # reuse identical answers (reproducibility)
    "max_explanations": 3,              # how many failing inputs get an LLM "WHY"
    "write_summary": True,              # let the LLM write a short student summary
}


class RulesError(Exception):
    """Raised when the rules file is missing something essential."""


@dataclass
class Rules:
    path: Path      # where the file was loaded from
    raw: dict       # parsed JSON (with the merged "llm" block)
    hash: str       # sha256 of canonical JSON

    # ----- convenient read-only views -------------------------------------
    @property
    def problem(self) -> str:
        return self.raw.get("problem", "unnamed_problem")

    @property
    def problem_statement(self) -> str:
        return self.raw.get("problem_statement", "")

    @property
    def time_limit_s(self) -> float:
        return float(self.raw.get("time_limit_s", 1))

    @property
    def memory_limit_mb(self) -> int:
        return int(self.raw.get("memory_limit_mb", 64))

    @property
    def metrics(self) -> list:
        return self.raw["metrics"]

    @property
    def llm_cfg(self) -> dict:
        return self.raw["llm"]

    @property
    def reference_solution(self):
        ref = self.raw.get("reference_solution")
        return self.resolve(ref) if ref else None

    def resolve(self, p) -> Path:
        """Turn a path written in the rules file into an absolute path."""
        p = Path(p)
        return p if p.is_absolute() else (PROJECT_ROOT / p)


def load_rules(path, llm_overrides: dict | None = None) -> Rules:
    """
    Read + validate a rules JSON file and return a Rules object.
    llm_overrides (e.g. {"model": "qwen2.5-coder:7b", "enabled": False}) are merged on top of the
    file's "llm" block BEFORE hashing, so the hash always describes the effective configuration.
    """
    path = Path(path)
    if not path.exists():
        raise RulesError(f"Rules file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RulesError(f"Rules file is not valid JSON: {e}") from e

    # ---- structural validation ------------------------------------------
    problems = []
    if not isinstance(raw.get("metrics"), list) or not raw["metrics"]:
        problems.append("'metrics' must be a non-empty list")
    else:
        names = set()
        for i, m in enumerate(raw["metrics"]):
            for key in ("name", "analyzer", "weight"):
                if key not in m:
                    problems.append(f"metric #{i} is missing '{key}'")
            if m.get("name") in names:
                problems.append(f"duplicate metric name '{m.get('name')}'")
            names.add(m.get("name"))
            m.setdefault("params", {})
            if not isinstance(m.get("weight", 0), (int, float)) or m.get("weight", 0) < 0:
                problems.append(f"metric '{m.get('name')}' has an invalid weight")
        total = sum(m.get("weight", 0) for m in raw["metrics"])
        if abs(total - 1.0) > 0.011:
            # Not fatal: the coordinator normalises by the total weight.
            raw.setdefault("_warnings", []).append(
                f"weights sum to {total:.2f}, not 1.00 - grade is normalised")
    if problems:
        raise RulesError("; ".join(problems))

    # ---- merge LLM defaults ----------------------------------------------
    raw["llm"] = {**DEFAULT_LLM, **raw.get("llm", {}), **(llm_overrides or {})}

    return Rules(path=path, raw=raw, hash="sha256:" + sha256_text(canonical_json(raw)))
