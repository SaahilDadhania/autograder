#!/usr/bin/env python3
"""
main.py  -  entry point of the AutoGrader (command line + launcher for the web UI)
====================================================================================

Examples
--------
  # grade one file (writes reports/add.json and reports/add.txt, prints the text report)
  python main.py --submission submissions/add.c --rules config/rules_add.json

  # grade every .c file in a folder (the "sample submissions" deliverable)
  python main.py --batch submissions/ --rules config/rules_add.json

  # run without Ollama (heuristic fallbacks for the LLM parts)
  python main.py --submission submissions/add.c --rules config/rules_add.json --no-llm

  # use another local model for this run
  python main.py --submission submissions/add.c --rules config/rules_add.json --model qwen2.5-coder:7b

  # start the Gradio web interface
  python main.py --ui

This file only parses arguments and calls the coordinator; all the logic lives in agent/.
"""
import argparse
import sys
from pathlib import Path

from agent.coordinator import grade_submission
from agent.report import render_text, save_report
from agent.rules import RulesError


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AutoGrader: rule-driven evaluator for C programs")
    p.add_argument("--submission", help="path of ONE student .c file")
    p.add_argument("--batch", help="folder: grade every .c file inside it")
    p.add_argument("--rules", default="config/rules_add.json", help="rules JSON file (default: %(default)s)")
    p.add_argument("--out", default="reports", help="output folder for reports (default: %(default)s)")
    p.add_argument("--model", help="override the Ollama model for this run, e.g. llama3.1:8b")
    p.add_argument("--no-llm", action="store_true", help="do not call Ollama (heuristic fallbacks only)")
    p.add_argument("--quiet", action="store_true", help="do not print the full text report")
    p.add_argument("--ui", action="store_true", help="launch the Gradio web interface instead")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # ---- web UI mode ------------------------------------------------------------
    if args.ui:
        from app import launch          # imported lazily so the CLI works without gradio installed
        launch()
        return 0

    if not args.submission and not args.batch:
        print("Give --submission FILE.c, --batch FOLDER or --ui.  See --help.")
        return 2

    # ---- per-run LLM overrides ----------------------------------------------------
    overrides = {}
    if args.no_llm:
        overrides["enabled"] = False
    if args.model:
        overrides["model"] = args.model

    files = [Path(args.submission)] if args.submission else sorted(Path(args.batch).glob("*.c"))
    if not files:
        print("No .c files found.")
        return 2

    summary_rows = []
    for f in files:
        print(f"\n>>> Grading {f} ...", file=sys.stderr)
        try:
            rep = grade_submission(f, args.rules, overrides, log=lambda m: print("   ", m, file=sys.stderr))
        except RulesError as e:
            print(f"Rules error: {e}")
            return 2
        jp, tp = save_report(rep, args.out)
        if not args.quiet:
            print(render_text(rep))
        summary_rows.append((f.name, rep["grade"], "PARTIAL" if rep["partial"] else "",
                             "; ".join(fl["type"] for fl in rep["flags"])))
        print(f"    reports written: {jp}  {tp}", file=sys.stderr)

    if len(files) > 1:                  # batch overview table
        print("\n===== Batch summary =====")
        print(f"{'file':<20}{'grade':>8}  {'status':<8} flags")
        for name, g, st, fl in summary_rows:
            print(f"{name:<20}{g:>8.1f}  {st:<8} {fl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
