"""
analyzers/function_split.py  -  metric "modularity"
=====================================================
Checks that the program is split into helper functions that are actually USED.

A helper counts only if its name is called somewhere other than its own definition.
score = 0.3 (it at least has main) + 0.7 * min(1, used_helpers / needed_helpers)
where needed_helpers = min_functions - 1  (main is not a helper).
"""
import re

from ..c_tools import mask_comments_strings, parse_functions
from ..common import AnalysisContext, AnalyzerResult
from .base import Analyzer, register


@register
class FunctionSplit(Analyzer):
    name = "function_split"

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        min_funcs = int(params.get("min_functions", 2))
        funcs = parse_functions(ctx.source_path, ctx.source_text)
        masked = mask_comments_strings(ctx.source_text)

        helpers = [f for f in funcs if f.name != "main"]
        # a call looks like  name(  ; the definition itself is one occurrence, so >1 means "called"
        used = [f.name for f in helpers if len(re.findall(rf"\b{re.escape(f.name)}\s*\(", masked)) > 1]
        needed = max(0, min_funcs - 1)

        if not params.get("expect_helpers", True) or needed == 0:
            score, fb = 1.0, "Helper functions not required for this problem."
        else:
            score = 0.3 + 0.7 * min(1.0, len(used) / needed)
            if len(used) >= needed:
                fb = f"Good: {len(used)} helper function(s) used ({', '.join(used)})."
            elif not helpers:
                fb = "Only main() is defined. Extract the core logic into a helper (e.g. int add(int, int))."
            else:
                fb = f"{len(helpers)} helper(s) defined but only {len(used)} called from the program."
        return AnalyzerResult(score, fb, {"functions_defined": len(funcs), "helpers_used": used,
                                          "expected_min": min_funcs})
