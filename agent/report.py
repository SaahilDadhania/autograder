"""
agent/report.py
================
Turns the report dict produced by the coordinator into:
    reports/<name>.json   -> for machines (full evidence)
    reports/<name>.txt    -> for the student (readable)
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path


def _wrap(text: str, indent: str = "    ", width: int = 78) -> str:
    return textwrap.fill(text, width=width, initial_indent=indent, subsequent_indent=indent)


def render_text(rep: dict) -> str:
    """Build the student-facing text report."""
    L = []
    L.append(f"===== Grading report for {rep['submission']} =====")
    L.append(f"Overall grade: {rep['grade']:.1f} / 100" + ("   (PARTIAL REPORT)" if rep["partial"] else ""))
    L.append(f"Problem: {rep['problem']}   Rules: {rep['rules_file']}   Rules hash: {rep['rules_hash'][:24]}...")
    fp = rep["first_pass"]
    L.append(f"First pass: compiled={fp['compiled']}  smoke_ok={fp['smoke_ok']}  "
             f"suspected_infinite_loop={fp['suspected_infinite_loop']}")
    for n in fp.get("notes", []):
        L.append(f"  ! {n}")
    L.append("")
    L.append(f"{'Metric':<15}{'Weight':>7}  {'Points':>9}  Comment")
    L.append("-" * 78)
    for m in rep["metrics"]:
        pts = f"{m['contribution']:.1f}/{m['weight'] * 100:.0f}"
        fb = ("(skipped) " if m["skipped"] else "") + m["feedback"]
        L.append(f"{m['name']:<15}{m['weight'] * 100:>6.0f}%  {pts:>9}  {fb[:90]}")

    if rep["flags"]:
        L += ["", "--- Flags for the instructor ---"]
        for f in rep["flags"]:
            L.append(f"  [{f.get('severity', 'info').upper()}] {f['message']}")

    if rep["failures"]:
        L += ["", "--- Where you failed ---"]
        for f in rep["failures"]:
            L.append(f"Test {f.get('test', '?')}   (found by: {f.get('source', '?')})")
            L.append(f"  INPUT:    {f['input']!r}")
            L.append(f"  EXPECTED: {f['expected']}")
            L.append(f"  GOT:      {f['got'] if f['got'] else '(nothing)'}   [{f['note']}]")
            L.append("  WHY:")
            L.append(_wrap(f.get("why", ""), "    "))
            L.append("")

    if rep["suggestions"]:
        L += ["--- Suggestions to raise the grade ---"]
        L += [textwrap.fill("- " + s, 78, subsequent_indent="  ") for s in rep["suggestions"]]

    if rep.get("summary"):
        L += ["", "--- Summary ---", _wrap(rep["summary"], "")]

    ul = rep.get("usefulness_limitations") or {}
    if ul:
        L += ["", "--- Usefulness & limitations of this evaluation ---", "Useful for:"]
        for i, u in enumerate(ul.get("usefulness", []), 1):
            L += [f"{i}. {u['use_case']}", _wrap("Example: " + u["example"]), _wrap("Benefit: " + u["benefit"])]
        L += ["", "Limits of this report:"]
        for i, u in enumerate(ul.get("limitations", []), 1):
            L += [f"{i}. {u['limitation']}", _wrap("Example: " + u["example"]), _wrap("Mitigation: " + u["mitigation"])]
    return "\n".join(L) + "\n"


def save_report(rep: dict, out_dir) -> tuple:
    """Write the .json and .txt files; returns (json_path, txt_path)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(rep["submission"]).stem
    jp, tp = out_dir / f"{stem}.json", out_dir / f"{stem}.txt"
    jp.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    tp.write_text(render_text(rep), encoding="utf-8")
    return jp, tp
