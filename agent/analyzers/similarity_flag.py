"""
analyzers/similarity_flag.py  -  metric "originality"
=======================================================
Flags suspected copying from classmates, online solutions or AI tools.

PART 1 - similarity (decides the score)
    * normalise the code: drop comments/#include, identifiers -> ID, numbers -> NUM,
      strings -> STR (so renaming variables or reformatting does not hide a copy)
    * cut the token stream into overlapping k-grams ("shingles", k = params["shingle"], default 4)
    * Jaccard similarity of the shingle sets against every .c file in
        corpus_dir               (other students' submissions)
        reference_corpus_dir     (OPTIONAL: known online / LLM-written solutions you collected)
    * max similarity >= flag_threshold  -> FLAG, score 0.   Otherwise score 1.
    * programs shorter than `min_tokens` (default 30) are never flagged: for trivial
      tasks independent students legitimately write identical code.

    A corpus file with the SAME FILE NAME as the submission is treated as the submission
    itself and skipped (so a submission stored inside the corpus does not match itself).

PART 2 - AI-style signals (ADVISORY ONLY, never changes the score)
    Deterministic signals (tutorial-style comments, over-commenting, boilerplate headers) and,
    if params["ask_llm"] is true, a cautious opinion from Ollama.  AI detection is unreliable -
    these only appear as a low-severity flag for a human to look at.
"""
import re
from pathlib import Path

from .. import prompts
from ..c_tools import normalized_tokens
from ..common import AnalysisContext, AnalyzerResult
from ..llm_client import LLMUnavailable, extract_json
from .base import Analyzer, register


def _shingles(tokens, k):
    return {tuple(tokens[i:i + k]) for i in range(max(0, len(tokens) - k + 1))}


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def ai_style_signals(src: str) -> list:
    """Cheap, deterministic hints that code *might* be AI-written (advisory)."""
    sig = []
    lines = [l for l in src.splitlines() if l.strip()]
    comments = [l for l in lines if l.lstrip().startswith(("//", "/*", "*"))]
    if lines and len(comments) / len(lines) > 0.4:
        sig.append("more than 40% of lines are comments")
    if re.search(r"(?i)//\s*(step\s*\d+|helper function|main function|function to|this function)", src):
        sig.append("tutorial-style comments ('Step 1', 'Function to ...')")
    if re.search(r"/\*\*[\s\S]*?@(param|return)", src):
        sig.append("full Doxygen blocks for a tiny exercise")
    if re.search(r"(?i)(here'?s|as an ai|note:|example usage)", src):
        sig.append("chat-assistant phrasing inside the file")
    return sig


@register
class SimilarityFlag(Analyzer):
    name = "similarity_flag"

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        k = int(params.get("shingle", 4))
        threshold = float(params.get("flag_threshold", 0.85))
        min_tokens = int(params.get("min_tokens", 30))
        mine_tokens = normalized_tokens(ctx.source_text)
        mine = _shingles(mine_tokens, k)

        # ---- gather corpus files -------------------------------------------------
        corpus = []
        for key in ("corpus_dir", "reference_corpus_dir"):
            if params.get(key):
                folder = ctx.rules.resolve(params[key])
                corpus += [(p, key) for p in sorted(folder.glob("*.c"))] if folder.is_dir() else []

        scores, flags = [], []
        for path, origin in corpus:
            if path.name == ctx.source_path.name:
                continue                                     # that's the submission itself
            sim = _jaccard(mine, _shingles(normalized_tokens(path.read_text(errors="replace")), k))
            scores.append((sim, path.name, origin))
        scores.sort(reverse=True)
        best = scores[0] if scores else (0.0, "-", "-")

        too_short = len(mine_tokens) < min_tokens
        flagged = (best[0] >= threshold) and not too_short
        if flagged:
            flags.append({"type": "similarity", "severity": "high", "other": best[1], "source": best[2],
                          "similarity": round(best[0], 2),
                          "message": f"Similarity {best[0]:.2f} with '{best[1]}' (threshold {threshold})."})

        # ---- advisory AI-style opinion --------------------------------------------
        signals = ai_style_signals(ctx.source_text)
        llm_opinion = None
        if params.get("ask_llm"):
            try:
                llm_opinion = extract_json(ctx.llm.chat(prompts.ORIGIN_SYSTEM, ctx.source_text, json_mode=True))
            except LLMUnavailable:
                llm_opinion = None
        if len(signals) >= 2 or (llm_opinion and llm_opinion.get("likely_ai") and
                                 float(llm_opinion.get("confidence", 0)) >= 0.8):
            flags.append({"type": "ai_style", "severity": "advisory",
                          "message": "Possible AI-generated style (advisory only): " + "; ".join(signals or ["LLM opinion"])})

        if flagged:
            fb = f"FLAGGED: similarity {best[0]:.2f} with '{best[1]}' (>= {threshold})."
        elif too_short:
            fb = f"Program is very short ({len(mine_tokens)} tokens): similarity is inconclusive, not flagged."
        else:
            fb = f"max similarity {best[0]:.2f} (safe)."
        return AnalyzerResult(0.0 if flagged else 1.0, fb, {
            "max_similarity": round(best[0], 3), "closest": best[1], "threshold": threshold,
            "tokens": len(mine_tokens), "inconclusive_short": too_short,
            "top_matches": [{"file": n, "similarity": round(s, 3), "corpus": o} for s, n, o in scores[:3]],
            "ai_style_signals": signals, "llm_opinion": llm_opinion}, flags=flags)
