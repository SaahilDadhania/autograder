"""
analyzers/static_analysis.py  -  metric "structure"
=====================================================
Measures code structure WITHOUT running it:
    * cyclomatic complexity per function  (<= max_cyclomatic)
    * function length in code lines       (<= max_function_length)
    * cppcheck findings (only if cppcheck is installed)

score = 0.4*complexity + 0.3*length + 0.3*cppcheck   (re-weighted if cppcheck is missing)
"""
import shutil
import subprocess

from ..c_tools import parse_functions
from ..common import AnalysisContext, AnalyzerResult
from .base import Analyzer, register


def _run_cppcheck(ctx: AnalysisContext):
    """Return a list of cppcheck finding strings, or None if cppcheck is unavailable."""
    if not shutil.which("cppcheck"):
        return None
    try:
        p = subprocess.run(
            ["cppcheck", "--enable=warning,style,performance,portability", "--std=c11", "--quiet",
             "--template={line}:{severity}:{id}:{message}", ctx.source_path.name],
            cwd=ctx.workdir, capture_output=True, text=True, timeout=60)
    except Exception:
        return None
    return [l for l in p.stderr.splitlines() if l.strip()]


@register
class StaticAnalysis(Analyzer):
    name = "static_analysis"

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        max_cc = int(params.get("max_cyclomatic", 8))
        max_len = int(params.get("max_function_length", 40))
        funcs = parse_functions(ctx.source_path, ctx.source_text)
        if not funcs:
            return AnalyzerResult(0.0, "No function definitions found.", {"functions": 0})

        worst_cc = max(f.cyclomatic for f in funcs)
        worst_len = max(f.nloc for f in funcs)
        # Full marks inside the limit; shrinks proportionally when over it.
        s_cc = min(1.0, max_cc / worst_cc)
        s_len = min(1.0, max_len / worst_len)

        findings = _run_cppcheck(ctx)
        if findings is None:
            score, cpp_note = 0.57 * s_cc + 0.43 * s_len, "cppcheck not installed (skipped)"
        else:
            s_cpp = max(0.0, 1.0 - 0.2 * len(findings))
            score, cpp_note = 0.4 * s_cc + 0.3 * s_len + 0.3 * s_cpp, f"{len(findings)} cppcheck finding(s)"

        issues = []
        for f in funcs:
            if f.cyclomatic > max_cc:
                issues.append(f"{f.name}(): cyclomatic {f.cyclomatic} > {max_cc}")
            if f.nloc > max_len:
                issues.append(f"{f.name}(): {f.nloc} lines > {max_len}")
        if findings:
            issues.append(findings[0])
        fb = "; ".join(issues) if issues else "Complexity and function length are within limits."
        return AnalyzerResult(score, fb, {
            "functions": [{"name": f.name, "cyclomatic": f.cyclomatic, "nloc": f.nloc} for f in funcs],
            "limits": {"max_cyclomatic": max_cc, "max_function_length": max_len},
            "cppcheck": cpp_note, "cppcheck_findings": (findings or [])[:10]})
