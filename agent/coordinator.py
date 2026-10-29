"""
agent/coordinator.py
=====================
The MAIN AGENT ("coordinator").  It owns the whole pipeline:

    load rules
      -> prepare workspace (copy source, compile reference solution)
      -> FIRST PASS  (compile check, smoke run, static sanity)
      -> DISPATCH    (run every analyzer named in the rules; parallel where safe)
      -> COLLATE     (weighted grade, failing inputs + explanations, suggestions, flags)
      -> return one report dict   (report.py turns it into JSON + text files)

The coordinator contains NO metric-specific logic.  Everything about a metric
lives in its analyzer; everything about thresholds/weights lives in the rules file.
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from .analyzers import REGISTRY
from .c_tools import compile_c, load_testset, static_sanity, tool_version
from .common import PROJECT_ROOT, AnalysisContext, AnalyzerResult
from .edge_cases import resolve_cases
from .llm_agents import explain_failure, write_summary
from .llm_client import OllamaClient
from .rules import Rules, load_rules
from .sandbox import run_sandboxed

MAX_FAILURES_SHOWN = 6          # how many failing inputs go into the "Where you failed" section


def _noop(_msg: str) -> None:
    pass


# ===========================================================================
# Step helpers
# ===========================================================================
def _prepare_reference(rules: Rules, workdir: Path, log) -> Optional[Path]:
    """Compile the instructor's reference solution once (used for expected outputs)."""
    ref_src = rules.reference_solution
    if not ref_src or not ref_src.exists():
        return None
    ref_dir = workdir / "ref"
    ref_dir.mkdir()
    (ref_dir / ref_src.name).write_text(ref_src.read_text())
    out = ref_dir / "ref.bin"
    res = compile_c(ref_dir / ref_src.name, out, ["-O2", "-std=c11"], ref_dir)
    if not res.ok:
        log(f"WARNING: reference solution failed to compile: {res.messages[:1]}")
        return None
    return out


def _collect_shared_inputs(rules: Rules) -> list:
    """Gather every known stdin text (hidden tests + edge cases) for the sanitizer analyzer."""
    inputs = []
    for m in rules.metrics:
        if m["analyzer"] == "io_tests":
            folder = rules.resolve(m["params"].get("testset_dir", ""))
            inputs += [(f"test:{t.name}", t.input) for t in load_testset(folder)] if folder.is_dir() else []
        elif m["analyzer"] == "edge_case_runner":
            inputs += [(f"edge:{label}", text) for label, text in resolve_cases(m["params"])]
    return inputs


def _first_pass(ctx: AnalysisContext, rules: Rules, log) -> dict:
    """
    The cheap sanity sweep BEFORE any expert runs:
        1. compile check   gcc -O2 -Wall -Wextra -std=c11
        2. smoke run       one input, time + memory capped
        3. static sanity   main present, reads input, prints output, returns
    """
    fp = {"compiled": False, "compile_warnings": 0, "smoke_ok": False,
          "suspected_infinite_loop": False, "static_sanity": static_sanity(ctx.source_text),
          "notes": []}

    # ---- 1. compile check -------------------------------------------------------
    binary = ctx.workdir / "sub.bin"
    comp = compile_c(ctx.source_path, binary, ["-O2", "-Wall", "-Wextra", "-std=c11"], ctx.workdir)
    fp["compiled"], fp["compile_warnings"] = comp.ok, comp.warnings
    if not comp.ok:
        fp["notes"].append("Compilation failed: " + (comp.messages[0] if comp.messages else comp.output[:200]))
        fp["compile_errors"] = comp.messages[:5]
        return fp
    ctx.binary = binary
    log(f"compile OK ({comp.warnings} warning(s))")

    # ---- 2. smoke run -----------------------------------------------------------
    smoke_input = rules.raw.get("smoke_input")
    if smoke_input is None:
        smoke_input = ctx.shared_inputs[0][1] if ctx.shared_inputs else ""
    r = run_sandboxed([str(binary)], smoke_input, rules.time_limit_s, rules.memory_limit_mb)
    fp["smoke_ok"] = r.ok
    fp["smoke_status"] = r.status
    if r.status == "TIMEOUT":
        fp["suspected_infinite_loop"] = True
        fp["notes"].append("Smoke run exceeded the time limit: suspected infinite loop or waiting for more input.")
    elif not r.ok:
        fp["notes"].append(f"Smoke run failed: {r.status} {r.signal_name}".strip())
    log(f"smoke run: {r.status}")

    # ---- 3. static sanity -------------------------------------------------------
    for key, msg in (("has_main", "no main() found"), ("reads_input", "never reads input"),
                     ("writes_output", "never prints output"), ("main_returns", "main() has no return statement")):
        if not fp["static_sanity"][key]:
            fp["notes"].append("Static sanity: " + msg)
    return fp


def _run_one(name: str, analyzer, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
    """Run a single analyzer; a crash inside it must never crash the whole agent."""
    try:
        res = analyzer.run(ctx, params)
        res.score = max(0.0, min(1.0, float(res.score)))        # clamp to [0, 1]
        return res
    except Exception as e:                                       # noqa: BLE001
        return AnalyzerResult(0.0, f"Analyzer '{name}' crashed: {e}",
                              {"error": str(e), "traceback": traceback.format_exc()[-800:]})


# ===========================================================================
# The public entry point
# ===========================================================================
def grade_submission(source_path, rules_path, llm_overrides: Optional[dict] = None,
                     log: Callable[[str], None] = _noop) -> dict:
    """
    Grade ONE C file under ONE rules file and return the full report dict.

    llm_overrides : e.g. {"model": "qwen2.5-coder:7b"} or {"enabled": False}
    log           : callback receiving short progress messages (CLI prints, UI shows)
    """
    source_path = Path(source_path).resolve()
    rules = load_rules(rules_path, llm_overrides)
    llm = OllamaClient(rules.llm_cfg)
    log(f"rules loaded ({rules.hash[:18]}...)  LLM {'on' if llm.is_available() else 'OFF (heuristic fallbacks)'}")

    with tempfile.TemporaryDirectory(prefix="autograder_") as tmp:
        workdir = Path(tmp)
        # copy the source into the private workdir, keeping its ORIGINAL file name
        local_src = workdir / source_path.name
        text = source_path.read_text(encoding="utf-8", errors="replace")
        local_src.write_text(text)

        ctx = AnalysisContext(source_path=local_src, source_text=text, workdir=workdir, rules=rules, llm=llm)
        # the corpus check must still know the ORIGINAL name -> same basename, so nothing special needed
        ctx.reference_binary = _prepare_reference(rules, workdir, log)
        ctx.shared_inputs = _collect_shared_inputs(rules)

        # ---------------- FIRST PASS -------------------------------------------------
        first_pass = _first_pass(ctx, rules, log)
        ctx.execution_allowed = first_pass["compiled"] and not first_pass["suspected_infinite_loop"]
        skip_reason = ("program does not compile" if not first_pass["compiled"]
                       else "suspected infinite loop - execution-based analyzers skipped")

        # ---------------- DISPATCH -----------------------------------------------------
        results: dict[str, AnalyzerResult] = {}
        plan = []                                          # [(metric_dict, analyzer_instance)]
        for m in rules.metrics:
            cls = REGISTRY.get(m["analyzer"])
            if cls is None:
                results[m["name"]] = AnalyzerResult(0.0, f"Unknown analyzer '{m['analyzer']}' in rules.",
                                                    {"error": "unknown analyzer",
                                                     "available": sorted(REGISTRY)}, skipped=True)
                continue
            inst = cls()
            if inst.requires_execution and not ctx.execution_allowed:
                results[m["name"]] = AnalyzerResult(0.0, f"Skipped: {skip_reason}.", skipped=True)
                continue
            plan.append((m, inst))

        # (a) analyzers that are safe to run side by side go to a small thread pool
        parallel = [(m, a) for m, a in plan if a.parallel_safe]
        serial = [(m, a) for m, a in plan if not a.parallel_safe]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {m["name"]: pool.submit(_run_one, m["name"], a, ctx, m["params"]) for m, a in parallel}
            for name, fut in futures.items():
                results[name] = fut.result()
                log(f"analyzer done: {name}")
        # (b) timing-sensitive analyzers run alone, afterwards
        for m, a in serial:
            results[m["name"]] = _run_one(m["name"], a, ctx, m["params"])
            log(f"analyzer done: {m['name']}")

        # ---------------- COLLATE ------------------------------------------------------
        total_w = sum(m["weight"] for m in rules.metrics) or 1.0
        metrics_out, grade = [], 0.0
        for m in rules.metrics:                            # rules order -> stable report order
            r = results[m["name"]]
            contribution = r.score * m["weight"] * 100 / total_w
            grade += contribution
            metrics_out.append({"name": m["name"], "analyzer": m["analyzer"], "score": round(r.score, 4),
                                "weight": m["weight"], "contribution": round(contribution, 2),
                                "skipped": r.skipped, "evidence": r.evidence, "feedback": r.feedback})
        grade = round(grade, 2)

        # failing inputs: de-duplicate by input text, keep rules order, cap the list
        failures, seen = [], set()
        for m in rules.metrics:
            for f in results[m["name"]].failures:
                if f["input"] not in seen:
                    seen.add(f["input"]); failures.append({**f, "metric": m["name"]})
        failures = failures[:MAX_FAILURES_SHOWN]
        for i, f in enumerate(failures):                   # the LLM explains only the first few (speed)
            if i < rules.llm_cfg["max_explanations"]:
                f["why"] = explain_failure(ctx, f)
            else:
                from .llm_agents import _fallback_explanation
                f["why"] = _fallback_explanation(f)

        flags = [fl for m in rules.metrics for fl in results[m["name"]].flags]
        if first_pass["suspected_infinite_loop"]:
            flags.append({"type": "suspected_infinite_loop", "severity": "high",
                          "message": "Smoke run timed out; execution-based metrics were skipped."})

        # suggestions: biggest point losses first
        lost = sorted((x for x in metrics_out if not x["skipped"] and x["score"] < 1
                       and x["evidence"].get("llm_used", True)),
                      key=lambda x: x["weight"] * (1 - x["score"]), reverse=True)
        suggestions = [f"[{x['name']}] {x['feedback']}" for x in lost[:5]]
        for x in metrics_out:                              # LLM readability tips are concrete: include them
            suggestions += [f"[{x['name']}] {s}" for s in x["evidence"].get("suggestions", [])]

        summary = write_summary(ctx, grade, metrics_out) if rules.llm_cfg["write_summary"] else ""

        # ---------------- tool versions (reproducibility) ------------------------------
        try:
            from importlib.metadata import version as pkg_version
            lizard_v = pkg_version("lizard")
        except Exception:
            lizard_v = "not installed (built-in parser used)"
        versions = {"gcc": tool_version(["gcc", "--version"]), "cppcheck": tool_version(["cppcheck", "--version"]),
                    "valgrind": tool_version(["valgrind", "--version"]), "lizard": lizard_v,
                    "python": sys.version.split()[0],
                    "ollama": llm.version(), "ollama_model": rules.llm_cfg["model"]}

        u_file = rules.resolve(rules.raw.get("usefulness_file", "config/usefulness_limitations.json"))
        usefulness = json.loads(u_file.read_text(encoding="utf-8")) if u_file.exists() else {}

        return {
            "submission": source_path.name,
            "problem": rules.problem,
            "rules_file": str(rules.path.name),
            "rules_hash": rules.hash,
            "grade": grade,
            "partial": not ctx.execution_allowed,
            "first_pass": first_pass,
            "metrics": metrics_out,
            "flags": flags,
            "failures": failures,
            "suggestions": suggestions,
            "summary": summary,
            "llm": {"enabled": rules.llm_cfg["enabled"], "available": llm.is_available(),
                    "model": rules.llm_cfg["model"], "temperature": rules.llm_cfg["temperature"],
                    "seed": rules.llm_cfg["seed"]},
            "rule_warnings": rules.raw.get("_warnings", []),
            "usefulness_limitations": usefulness,
            "tool_versions": versions,
        }
