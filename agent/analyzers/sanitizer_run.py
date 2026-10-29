"""
analyzers/sanitizer_run.py  -  metric "memory_safety"
=======================================================
Compiles the program with AddressSanitizer + UndefinedBehaviorSanitizer (and
LeakSanitizer, which is part of ASan on Linux) and runs it on EVERY known input
(hidden tests + edge cases, collected by the coordinator in ctx.shared_inputs).

It reports:
    * "runtime error: ..."          -> UBSan  (signed overflow, bad shifts, ...)
    * "ERROR: AddressSanitizer: ..."-> ASan   (buffer overflow, use-after-free, ...)
    * "detected memory leaks"       -> LSan   (malloc without free)
and, if params["valgrind"] is true and valgrind is installed, a leak check with valgrind.

score = 1 - 0.34 per DISTINCT issue (min 0).  The same bug seen on 5 inputs counts once.

NOTE: ASan needs a huge virtual address space, so this analyzer runs WITHOUT the
RLIMIT_AS memory cap (the wall-clock/CPU limits still apply).
"""
import re
import shlex
import shutil

from ..c_tools import compile_c
from ..common import AnalysisContext, AnalyzerResult
from ..sandbox import run_sandboxed
from .base import Analyzer, register

_UB_RE = re.compile(r"(\S+\.c:\d+:\d+): runtime error: (.+)")
_ASAN_RE = re.compile(r"ERROR: AddressSanitizer: (\S+)")


@register
class SanitizerRun(Analyzer):
    name = "sanitizer_run"
    requires_execution = True

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        flags = shlex.split(params.get("flags", "-fsanitize=address,undefined"))
        binary = ctx.workdir / "sub_san.bin"
        comp = compile_c(ctx.source_path, binary, ["-g", "-O1", "-fno-omit-frame-pointer", "-std=c11", *flags],
                         ctx.workdir)
        if not comp.ok:
            return AnalyzerResult(0.0, "Sanitizer build failed to compile.", {"messages": comp.messages[:5]})

        # inputs: everything the coordinator knows; fall back to one trivial input
        inputs = ctx.shared_inputs or [("smoke", "")]
        issues = {}                                        # message -> (first input label, input)
        env = {"ASAN_OPTIONS": "detect_leaks=1:color=never", "UBSAN_OPTIONS": "print_stacktrace=0:color=never"}
        failures, lsan_broken = [], False

        for label, text in inputs:
            r = run_sandboxed([str(binary)], text, ctx.rules.time_limit_s * 10, None, nproc=None, env_extra=env)
            err = r.stderr
            if "LeakSanitizer has encountered a fatal error" in err:
                lsan_broken = True                          # e.g. inside restricted containers (ptrace)
            # keep only the KIND of UB ("signed integer overflow") + source location, so the same bug
            # triggered by different input values is counted once
            found = [f"UBSan: {m.group(2).split(':')[0].strip()} at {m.group(1)}" for m in _UB_RE.finditer(err)]
            found += [f"ASan: {m.group(1)}" for m in _ASAN_RE.finditer(err)]
            if "detected memory leaks" in err:
                found.append("LSan: memory leak (malloc without free)")
            for msg in found:
                if msg not in issues:
                    issues[msg] = (label, text)
                    failures.append({"test": label, "input": text, "expected": "no sanitizer diagnostics",
                                     "got": "", "status": "SANITIZER", "note": msg, "stderr": err.strip()[:300],
                                     "source": "sanitizer"})

        # ---- optional valgrind cross-check ---------------------------------------
        valgrind_note = None
        if params.get("valgrind") and shutil.which("valgrind"):
            plain = ctx.workdir / "sub_dbg.bin"
            if compile_c(ctx.source_path, plain, ["-g", "-O0", "-std=c11"], ctx.workdir).ok:
                label, text = inputs[0]
                v = run_sandboxed(["valgrind", "--leak-check=full", "--error-exitcode=99", "-q", str(plain)],
                                  text, ctx.rules.time_limit_s * 30, None, nproc=None)
                if v.returncode == 99 or "definitely lost" in v.stderr:
                    issues["valgrind: leak or invalid access"] = (label, text)
                    valgrind_note = v.stderr.strip()[:300]
                else:
                    valgrind_note = "valgrind clean"

        score = max(0.0, 1.0 - 0.34 * len(issues))
        fb = "ASan/UBSan clean on all inputs." if not issues else \
            f"{len(issues)} issue(s): " + "; ".join(list(issues)[:3])
        return AnalyzerResult(score, fb, {"inputs_checked": len(inputs), "issues": list(issues),
                                          "leaksanitizer_unavailable": lsan_broken, "valgrind": valgrind_note},
                              failures=failures)
