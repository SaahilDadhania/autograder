"""
analyzers/complexity_estimator.py  -  metric "complexity"
===========================================================
Estimates time complexity EMPIRICALLY ("timing curve"):

    1. build one input per size n in params["sizes"]   (generator, see below)
    2. run the student's binary `repeats` times per size, take the MEDIAN CPU time
    3. fit a straight line through (log n, log time); its SLOPE is the growth exponent
    4. map slope -> class:  <0.25 O(1) | <0.5 O(log n) | <1.2 O(n) | <1.5 O(n log n) | <2.4 O(n^2) | else O(n^3)
    5. score 1.0 if observed class <= required class, otherwise 0.4 less per class too slow

INPUT GENERATORS (params["generator"]):
    "constant"   (default) the same input for every n -> only meaningful for O(1) tasks
                 such as 'add two integers'
    "n_ints"     first line n, then n pseudo-random ints (seeded -> reproducible)
    "<path>.py"  instructor-supplied file that defines  generate(n) -> str

HONEST LIMITATION: if every run finishes below `noise_floor_ms` (default 30 ms) the
timing is dominated by process start-up noise.  We then report the estimate as
INCONCLUSIVE (and give full marks only if the requirement is not violated by the data
we could measure).  Use larger sizes in the rules for a meaningful verdict.

This analyzer must run ALONE (parallel_safe = False) so other work doesn't distort timings.
"""
import importlib.util
import math
import random
import statistics

from ..common import AnalysisContext, AnalyzerResult
from ..sandbox import run_sandboxed
from .base import Analyzer, register

ORDER = ["O(1)", "O(log n)", "O(n)", "O(n log n)", "O(n^2)", "O(n^3)"]
ALIASES = {"o(1)": 0, "o(logn)": 1, "o(n)": 2, "o(nlogn)": 3, "o(n^2)": 4, "o(n2)": 4, "o(n^3)": 5}


def _classify(slope: float) -> int:
    for rank, bound in enumerate([0.25, 0.5, 1.2, 1.5, 2.4]):
        if slope < bound:
            return rank
    return 5


def _make_input(generator: str, n: int, ctx: AnalysisContext) -> str:
    if generator == "n_ints":
        rng = random.Random(1000 + n)                     # fixed seed -> reproducible
        return f"{n}\n" + " ".join(str(rng.randint(-10**6, 10**6)) for _ in range(n)) + "\n"
    if generator.endswith(".py"):                         # instructor's own generator
        spec = importlib.util.spec_from_file_location("gen", ctx.rules.resolve(generator))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        return mod.generate(n)
    return "7 5\n"                                        # "constant"


@register
class ComplexityEstimator(Analyzer):
    name = "complexity_estimator"
    requires_execution = True
    parallel_safe = False

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        required = params.get("required", "O(1)")
        req_rank = ALIASES.get(required.replace(" ", "").lower())
        if req_rank is None:
            return AnalyzerResult(0.0, f"Unknown required complexity '{required}'.", {"error": "bad rule"}, skipped=True)
        sizes = params.get("sizes", [1, 10, 100])
        repeats = int(params.get("repeats", 5))
        floor_ms = float(params.get("noise_floor_ms", 30))
        generator = params.get("generator", "constant")

        curve = []                                         # [(n, median_cpu_ms)]
        for n in sizes:
            text = _make_input(generator, n, ctx)
            times = []
            for _ in range(repeats):
                r = run_sandboxed([str(ctx.binary)], text, ctx.rules.time_limit_s * 5, ctx.rules.memory_limit_mb)
                if not r.ok:
                    return AnalyzerResult(0.0, f"Program failed at n={n} ({r.status}); cannot estimate.",
                                          {"failed_at_n": n, "status": r.status})
                times.append(r.cpu_s * 1000)
            curve.append((n, statistics.median(times)))

        max_ms = max(t for _, t in curve)
        inconclusive = max_ms < floor_ms or len(set(sizes)) < 2 or generator == "constant"
        # least-squares slope on log-log scale (floor of 0.5 ms avoids log(0))
        xs = [math.log(n) for n, _ in curve]
        ys = [math.log(max(t, 0.5)) for _, t in curve]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        denom = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom if denom else 0.0

        observed = 0 if inconclusive else _classify(slope)
        score = 1.0 if observed <= req_rank else max(0.0, 1.0 - 0.4 * (observed - req_rank))
        if inconclusive:
            fb = (f"Consistent with {required}: all runs finished below the {floor_ms:.0f} ms timing noise floor "
                  f"(max {max_ms:.1f} ms), so growth could not be measured - use larger sizes for a firm verdict.")
        elif observed <= req_rank:
            fb = f"{ORDER[observed]} observed (slope {slope:.2f}); required {required} - OK."
        else:
            fb = f"{ORDER[observed]} observed (slope {slope:.2f}) but {required} is required."
        return AnalyzerResult(score, fb, {
            "required": required, "observed": ORDER[observed], "slope": round(slope, 3),
            "inconclusive": inconclusive, "generator": generator,
            "curve_ms": [{"n": n, "median_cpu_ms": round(t, 3)} for n, t in curve]})
