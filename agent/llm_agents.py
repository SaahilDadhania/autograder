"""
agent/llm_agents.py
====================
The LLM-powered helper agents.  Each is a small function: build a prompt (system
prompt from prompts.py), call Ollama, parse + VALIDATE the answer.

IMPORTANT DESIGN RULE: the LLM never decides a grade on its own authority.
    * test generator  -> proposes inputs, but the expected output comes from the
                         reference program, and the grade only changes if the rules say so
    * explainer       -> only writes text ("WHY"), never changes a score
    * summary writer  -> only writes text
    * readability     -> returns a number, but it is clamped to 0-10 and cached
Every function has a deterministic fallback so the agent still works offline.
"""
from __future__ import annotations

from typing import List

from . import prompts
from .common import AnalysisContext
from .llm_client import LLMUnavailable, extract_json
from .sandbox import run_sandboxed
from .c_tools import outputs_match


# ---------------------------------------------------------------------------
# Counter-test generator
# ---------------------------------------------------------------------------
def generate_counter_tests(ctx: AnalysisContext, already_tried: List[str], n: int) -> List[dict]:
    """
    Ask the LLM for up to `n` NEW inputs that might break the student's program.
    Returns [{"input", "reason"}] after cleaning.  Returns [] if the LLM is down.
    """
    user = (f"PROBLEM:\n{ctx.rules.problem_statement or ctx.rules.problem}\n\n"
            f"STUDENT CODE:\n{ctx.source_text}\n\n"
            f"INPUTS ALREADY TRIED (do not repeat):\n" + "\n".join(repr(x) for x in already_tried[:30]) +
            f"\n\nPropose up to {n} new inputs.")
    try:
        raw = ctx.llm.chat(prompts.TESTGEN_SYSTEM, user, json_mode=True)
    except LLMUnavailable:
        return []
    data = extract_json(raw) or {}
    cleaned, seen = [], set(already_tried)
    for c in data.get("cases", [])[:n * 2]:
        text = c.get("input") if isinstance(c, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 2000 or text in seen:
            continue                      # drop empty / huge / duplicate inputs
        seen.add(text)
        cleaned.append({"input": text, "reason": str(c.get("reason", ""))[:200]})
        if len(cleaned) == n:
            break
    return cleaned


# ---------------------------------------------------------------------------
# Failure explainer
# ---------------------------------------------------------------------------
def _fallback_explanation(f: dict) -> str:
    """Deterministic 'WHY' used when the LLM is unavailable."""
    err = (f.get("stderr") or "") + " " + (f.get("note") or "")
    if "signed integer overflow" in err:
        return ("a + b overflows a 32-bit signed int (undefined behaviour in C). Use a wider type such as "
                "long long, or check that b <= INT_MAX - a before adding.")
    # expected value outside the 32-bit range but the program printed something else -> classic overflow
    try:
        if abs(int(str(f.get("expected", "")).strip())) > 2**31 - 1 and f.get("status") == "OK":
            return ("The true result does not fit in a 32-bit int, so the addition overflowed and wrapped around "
                    "(undefined behaviour in C). Use a wider type such as long long (and %lld), or check "
                    "b <= INT_MAX - a before adding.")
    except ValueError:
        pass
    if f.get("status") == "TIMEOUT":
        return "The program did not finish within the time limit - look for an infinite loop or a very slow algorithm."
    if f.get("status") == "RUNTIME_ERROR":
        return "The program crashed. Check array bounds, NULL pointers and uninitialised variables."
    if f.get("status") == "OK" or f.get("note") == "wrong answer":
        return "The printed result differs from the expected one. Re-check the arithmetic/format specifiers for this input."
    return "See the sanitizer / compiler message above."


def explain_failure(ctx: AnalysisContext, f: dict) -> str:
    """One short LLM explanation of a failing input (falls back to a rule-based one)."""
    user = (f"PROBLEM:\n{ctx.rules.problem_statement or ctx.rules.problem}\n\n"
            f"STUDENT CODE:\n{ctx.source_text}\n\n"
            f"FAILING INPUT: {f.get('input')!r}\nEXPECTED OUTPUT: {f.get('expected')!r}\n"
            f"ACTUAL OUTPUT: {f.get('got')!r}\nNOTE: {f.get('note')}\nSTDERR: {f.get('stderr', '')}")
    try:
        text = ctx.llm.chat(prompts.EXPLAIN_SYSTEM, user, num_predict=200)
        return " ".join(text.split())[:600] or _fallback_explanation(f)
    except LLMUnavailable:
        return _fallback_explanation(f)


# ---------------------------------------------------------------------------
# Student summary
# ---------------------------------------------------------------------------
def write_summary(ctx: AnalysisContext, grade: float, metrics: list) -> str:
    """3-4 sentence closing paragraph; '' if the LLM is unavailable."""
    table = "\n".join(f"- {m['name']}: {m['score']*100:.0f}% (weight {m['weight']*100:.0f}%) - {m['feedback']}"
                      for m in metrics)
    try:
        text = ctx.llm.chat(prompts.SUMMARY_SYSTEM, f"OVERALL GRADE: {grade}/100\n{table}", num_predict=220)
        return " ".join(text.split())
    except LLMUnavailable:
        return ""
