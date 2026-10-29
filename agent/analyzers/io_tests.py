"""
analyzers/io_tests.py  -  metric "correctness"
================================================
Runs the student's binary on every hidden test (tests/<problem>/NN.in + NN.out)
inside the sandbox and compares stdout with the expected output.

score = tests_passed / tests_total

Also records a tiny performance profile (max CPU time and peak memory) for the
report - this is the "profiling" part of the assignment, done with the kernel's
own rusage counters instead of perf/oprofile so it works unprivileged.
"""
from ..c_tools import load_testset, outputs_match
from ..common import AnalysisContext, AnalyzerResult
from ..sandbox import run_sandboxed
from .base import Analyzer, register


@register
class IOTests(Analyzer):
    name = "io_tests"
    requires_execution = True

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        folder = ctx.rules.resolve(params["testset_dir"])
        tests = load_testset(folder)
        if not tests:
            return AnalyzerResult(0.0, f"No tests found in {folder} (need NAME.in + NAME.out).",
                                  {"error": "empty testset"}, skipped=True)
        mode = params.get("compare", "lines")
        passed, failures, max_cpu, max_rss = 0, [], 0.0, 0

        for t in tests:
            r = run_sandboxed([str(ctx.binary)], t.input, ctx.rules.time_limit_s,
                              ctx.rules.memory_limit_mb)
            max_cpu, max_rss = max(max_cpu, r.cpu_s), max(max_rss, r.max_rss_kb)
            if r.ok and outputs_match(t.expected, r.stdout, mode):
                passed += 1
                continue
            # ---- build a precise failure record -------------------------------
            if r.status == "OK":
                note = "wrong answer"
            else:
                note = {"TIMEOUT": "time limit exceeded", "MEMORY_LIMIT": "memory limit exceeded",
                        "OUTPUT_LIMIT": "printed too much output"}.get(
                            r.status, f"crashed ({r.signal_name or 'exit code ' + str(r.returncode)})")
            failures.append({"test": t.name, "input": t.input, "expected": t.expected.strip(),
                             "got": r.stdout.strip(), "status": r.status, "note": note,
                             "stderr": r.stderr.strip()[:300], "source": "hidden test"})

        total = len(tests)
        fb = f"{passed}/{total} tests pass."
        if failures:
            fb += f" First failure: input {failures[0]['input'].strip()!r} -> {failures[0]['note']}."
        return AnalyzerResult(
            passed / total, fb,
            evidence={"tests_passed": passed, "tests_total": total,
                      "failing": [{k: f[k] for k in ("test", "input", "expected", "got", "note")}
                                  for f in failures[:10]],
                      "profile": {"max_cpu_ms": round(max_cpu * 1000, 2),
                                  "peak_rss_mb": round(max_rss / 1024, 2)}},
            failures=failures)
