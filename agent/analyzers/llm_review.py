"""
analyzers/llm_review.py  -  metric "readability"   (the Ollama "evaluator agent")
===================================================================================
Asks the local LLM to grade readability with a fixed rubric (see prompts.py), then
BLENDS the answer with a tiny deterministic heuristic:

    score = (1 - blend) * LLM_score + blend * heuristic_score        (blend default 0.3)

Why blend?  Small local models are noisy judges.  The heuristic (comment density,
identifier length, line length, indentation) anchors the result so a flaky model
answer cannot swing the grade much.  Set params["heuristic_blend"] = 0 for pure LLM.

If Ollama is unavailable the heuristic alone is used and the report says so.
"""
import re

from .. import prompts
from ..c_tools import mask_comments_strings, strip_comments
from ..common import AnalysisContext, AnalyzerResult
from ..llm_client import LLMUnavailable, extract_json
from .base import Analyzer, register


def readability_heuristic(src: str):
    """Deterministic 0..1 readability estimate + the numbers behind it."""
    lines = [l for l in src.splitlines() if l.strip()]
    n = max(1, len(lines))
    comment_lines = sum(1 for l in lines if l.lstrip().startswith(("//", "/*", "*")))
    masked = mask_comments_strings(src)
    idents = [w for w in re.findall(r"\b[A-Za-z_]\w*\b", masked)
              if w not in {"int", "char", "void", "return", "if", "for", "while", "else", "main", "include",
                           "stdio", "stdlib", "string", "printf", "scanf", "h", "long", "double", "float"}]
    declared = re.findall(r"\b(?:int|long|char|double|float|unsigned|size_t)\s+\**\s*([A-Za-z_]\w*)", masked)
    avg_name = sum(len(d) for d in declared) / len(declared) if declared else 3
    long_lines = sum(1 for l in lines if len(l) > 100)
    indented = sum(1 for l in lines if l.startswith((" ", "\t")))
    # each component is capped at 1.0
    c_comments = min(1.0, (comment_lines / n) / 0.15)        # ~1 comment per 7 lines is "enough"
    c_names = min(1.0, avg_name / 5)                         # average 5+ chars per variable
    c_width = 1.0 - min(1.0, long_lines / n * 5)
    c_indent = min(1.0, indented / max(1, n - 3))            # bodies are indented
    score = 0.35 * c_comments + 0.30 * c_names + 0.15 * c_width + 0.20 * c_indent
    return score, {"comment_ratio": round(comment_lines / n, 2), "avg_variable_name_length": round(avg_name, 1),
                   "long_lines": long_lines, "indented_ratio": round(indented / n, 2)}


@register
class LLMReview(Analyzer):
    name = "llm_review"

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        h_score, h_info = readability_heuristic(ctx.source_text)
        blend = float(params.get("heuristic_blend", 0.3))
        numbered = "\n".join(f"{i + 1:3d}| {l}" for i, l in enumerate(ctx.source_text.splitlines()))
        try:
            raw = ctx.llm.chat(prompts.READABILITY_SYSTEM, f"C SOURCE FILE:\n{numbered}", json_mode=True,
                               model=params.get("model"), temperature=params.get("temperature"),
                               seed=params.get("seed"))
        except LLMUnavailable as e:
            return AnalyzerResult(h_score, "LLM unavailable - heuristic readability only "
                                           f"(comments {h_info['comment_ratio']:.0%}, avg name length {h_info['avg_variable_name_length']}).",
                                  {"llm_used": False, "reason": str(e), "heuristic": h_info})
        data = extract_json(raw) or {}
        try:
            llm_score = max(0, min(10, int(round(float(data["score"]))))) / 10      # clamp 0..10
        except (KeyError, TypeError, ValueError):
            return AnalyzerResult(h_score, "LLM answer was not valid JSON - heuristic readability used.",
                                  {"llm_used": False, "raw_response": raw[:500], "heuristic": h_info})
        score = (1 - blend) * llm_score + blend * h_score
        reasons = [str(r) for r in data.get("reasons", [])][:3]
        suggestions = [str(s) for s in data.get("suggestions", [])][:3]
        fb = "; ".join(reasons) if reasons else "Readability reviewed by the LLM."
        return AnalyzerResult(score, fb, {
            "llm_used": True, "model": params.get("model") or ctx.llm.cfg["model"],
            "temperature": params.get("temperature", ctx.llm.cfg["temperature"]),
            "seed": params.get("seed", ctx.llm.cfg["seed"]),
            "llm_score": llm_score, "heuristic_score": round(h_score, 2), "blend": blend,
            "criteria": {k: data.get(k) for k in ("naming", "comments", "formatting", "structure")},
            "suggestions": suggestions, "raw_response": raw[:1000], "heuristic": h_info})
