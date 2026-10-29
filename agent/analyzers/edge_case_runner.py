"""
analyzers/edge_case_runner.py  -  metric "logical"
====================================================
Differential testing on corner cases:

    1. take the named edge cases from the rules (negatives, zero, int_overflow, ...)
    2. run the instructor's REFERENCE solution -> expected output
    3. run the student's program on the same input
    4. any difference (or crash / timeout) is a failure with the exact input shown

OPTIONAL "counter-test agent": if params["llm_cases"] = N (> 0) the local LLM reads
the student's code and proposes N extra inputs aimed at its weak spots.  Each one is
validated by the reference solution first.  By default these are REPORTED ONLY and do
not change the grade (params["llm_cases_affect_grade"] = true makes them count).
That keeps the grade reproducible even though LLM suggestions can vary by model.
"""
from ..c_tools import outputs_match
from ..common import AnalysisContext, AnalyzerResult
from ..edge_cases import BUILTIN_CASES, resolve_cases
from ..llm_agents import generate_counter_tests
from ..sandbox import run_sandboxed
from .base import Analyzer, register


def _compare_on(ctx: AnalysisContext, label: str, text: str, source: str):
    """Run reference + student on one input. Returns (counted, failure_dict_or_None)."""
    ref = run_sandboxed([str(ctx.reference_binary)], text, ctx.rules.time_limit_s * 3, None)
    if not ref.ok:
        return False, None                      # reference itself can't handle it -> invalid test, skip
    stu = run_sandboxed([str(ctx.binary)], text, ctx.rules.time_limit_s, ctx.rules.memory_limit_mb)
    if stu.ok and outputs_match(ref.stdout, stu.stdout):
        return True, None
    note = "wrong answer" if stu.ok else stu.status.lower().replace("_", " ")
    if not stu.ok and stu.signal_name:
        note += f" ({stu.signal_name})"
    return True, {"test": label, "input": text, "expected": ref.stdout.strip(), "got": stu.stdout.strip(),
                  "status": stu.status, "note": note, "stderr": stu.stderr.strip()[:300], "source": source}


@register
class EdgeCaseRunner(Analyzer):
    name = "edge_case_runner"
    requires_execution = True

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        if ctx.reference_binary is None:
            return AnalyzerResult(0.0, "No reference_solution in the rules (or it failed to compile): "
                                       "cannot compute expected outputs.", {"error": "no reference"}, skipped=True)

        cases = resolve_cases(params)
        unknown = [c for c in params.get("cases", [])
                   if c not in BUILTIN_CASES and c not in params.get("custom_cases", {})]
        counted = passed = 0
        failures = []
        for label, text in cases:
            ok_counted, fail = _compare_on(ctx, f"edge:{label}", text, "edge case")
            counted += ok_counted
            if ok_counted and fail is None:
                passed += 1
            elif fail:
                failures.append(fail)

        # ---- optional LLM counter-test agent --------------------------------
        llm_found, n_llm = [], int(params.get("llm_cases", 0))
        llm_counted = llm_passed = 0
        if n_llm > 0:
            tried = [t for _, t in cases] + [t for _, t in ctx.shared_inputs]
            for k, c in enumerate(generate_counter_tests(ctx, tried, n_llm)):
                ok_counted, fail = _compare_on(ctx, f"llm-case#{k + 1}", c["input"], "LLM counter-test")
                llm_counted += ok_counted
                if ok_counted and fail is None:
                    llm_passed += 1
                elif fail:
                    fail["reason"] = c["reason"]
                    llm_found.append(fail)
            if params.get("llm_cases_affect_grade", False):
                counted += llm_counted; passed += llm_passed
                failures += llm_found

        score = passed / counted if counted else 0.0
        fb = (f"{passed}/{counted} edge cases handled." if counted else "No usable edge cases ran.")
        if failures:
            fb += f" Fails on {failures[0]['input'].strip()!r} ({failures[0]['note']})."
        if llm_found and not params.get("llm_cases_affect_grade", False):
            fb += f" The counter-test agent also found {len(llm_found)} failing input(s) (not graded)."
        return AnalyzerResult(
            score, fb,
            evidence={"cases_run": counted, "cases_passed": passed, "unknown_case_names": unknown,
                      "failing": [{k: f[k] for k in ("test", "input", "expected", "got", "note")} for f in failures[:10]],
                      "llm_counter_tests": {"proposed_valid": llm_counted, "failing": llm_found[:10]}},
            failures=failures + (llm_found if not params.get("llm_cases_affect_grade", False) else []))
