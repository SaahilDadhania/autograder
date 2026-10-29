"""
analyzers/compiler_warnings.py  -  metric "syntax"
====================================================
Compiles the source with the instructor's flags (e.g. -Wall -Wextra -std=c11) and
counts warnings.  We REMOVE -Werror before counting (so gcc keeps going and reports
every warning) but remember whether -Werror would have rejected the file.

score = 1.0 - 0.1 per warning (min 0);   any compile ERROR -> 0.
This is a source-only analyzer: it still runs when the program cannot execute.
"""
import shlex

from ..c_tools import compile_c
from ..common import AnalysisContext, AnalyzerResult
from .base import Analyzer, register


@register
class CompilerWarnings(Analyzer):
    name = "compiler_warnings"

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        flags = shlex.split(params.get("flags", "-Wall -Wextra -std=c11"))
        had_werror = "-Werror" in flags
        flags = [f for f in flags if f != "-Werror"]
        res = compile_c(ctx.source_path, None, ["-O2", *flags], ctx.workdir)

        if res.errors or not res.ok:
            return AnalyzerResult(0.0, "Does not compile: " + (res.messages[0] if res.messages else "gcc failed"),
                                  {"errors": res.errors, "messages": res.messages[:10]})
        per_warning = float(params.get("penalty_per_warning", 0.1))
        score = max(0.0, 1.0 - per_warning * res.warnings)
        fb = "No warnings." if not res.warnings else \
            f"{res.warnings} warning(s), e.g. {res.messages[0].split(': ', 1)[-1]}"
        return AnalyzerResult(score, fb, {"warnings": res.warnings, "messages": res.messages[:10],
                                          "flags": flags,
                                          "would_fail_with_Werror": bool(had_werror and res.warnings)})
