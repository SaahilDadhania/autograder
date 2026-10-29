#!/usr/bin/env python3
"""
app.py  -  Gradio web interface of the AutoGrader
===================================================
Run with:   python app.py         (or:  python main.py --ui)
Open:       http://127.0.0.1:7860

The UI is only a thin shell:
    1. the user uploads a .c file
    2. picks a rule set (the JSON is shown in an editor, so the instructor can change
       weights / thresholds right here without touching any Python code)
    3. presses "Grade" -> we call agent.coordinator.grade_submission() and display the result

All grading logic stays in agent/.  `run_grading` below has NO gradio dependency, so it can
be unit-tested without a browser; gradio is imported lazily inside build_ui().
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from agent.common import PROJECT_ROOT
from agent.coordinator import grade_submission
from agent.llm_client import OllamaClient
from agent.report import render_text, save_report
from agent.rules import DEFAULT_LLM, RulesError

CONFIG_DIR = PROJECT_ROOT / "config"


# ===========================================================================
# Backend functions (no gradio here)
# ===========================================================================
def list_rule_files() -> list:
    """Names of all rule sets available in config/ (files called rules_*.json)."""
    return sorted(p.name for p in CONFIG_DIR.glob("rules_*.json"))


def load_rules_text(rule_name: str) -> str:
    """Return the raw JSON text of a rule set (shown in the editor box)."""
    return (CONFIG_DIR / rule_name).read_text(encoding="utf-8") if rule_name else ""


def check_ollama(model: str) -> str:
    """Tell the user whether Ollama is reachable and the model is pulled."""
    cfg = {**DEFAULT_LLM, **({"model": model.strip()} if model and model.strip() else {})}
    client = OllamaClient(cfg)
    if client.is_available():
        return f"Ollama {client.version()} is running and model '{cfg['model']}' is available."
    return (f"Ollama model '{cfg['model']}' is NOT reachable at {cfg['host']}.\n"
            f"Start the server:  ollama serve\nDownload the model: ollama pull {cfg['model']}")


def run_grading(source_file, rules_text: str, model: str, use_llm: bool):
    """
    Grade the uploaded file with the (possibly edited) rules.
    Returns: (headline, table_rows, text_report, json_report, download_files, progress_log)
    """
    if not source_file:
        return "Please upload a .c file first.", [], "", {}, None, ""
    try:
        json.loads(rules_text)                       # fail early with a friendly message
    except json.JSONDecodeError as e:
        return f"The rules JSON is invalid: {e}", [], "", {}, None, ""

    # write the edited rules to a temp file (relative paths inside still resolve against the project root)
    with tempfile.NamedTemporaryFile("w", suffix=".json", prefix="rules_", delete=False, encoding="utf-8") as tf:
        tf.write(rules_text)
        rules_path = tf.name

    overrides = {"enabled": bool(use_llm)}
    if model and model.strip():
        overrides["model"] = model.strip()
    log_lines = []
    try:
        report = grade_submission(source_file, rules_path, overrides, log=log_lines.append)
    except RulesError as e:
        return f"Rules error: {e}", [], "", {}, None, "\n".join(log_lines)
    finally:
        os.unlink(rules_path)

    out_dir = PROJECT_ROOT / "reports"
    jp, tp = save_report(report, out_dir)
    headline = (f"## Grade: {report['grade']:.1f} / 100" + ("  (partial report)" if report["partial"] else "") +
                ("\n\n**Flags:** " + "; ".join(f["message"] for f in report["flags"]) if report["flags"] else ""))
    rows = [[m["name"], f"{m['weight'] * 100:.0f}%", f"{m['contribution']:.1f} / {m['weight'] * 100:.0f}",
             ("(skipped) " if m["skipped"] else "") + m["feedback"]] for m in report["metrics"]]
    return headline, rows, render_text(report), report, [str(jp), str(tp)], "\n".join(log_lines)


# ===========================================================================
# Gradio layout
# ===========================================================================
def build_ui():
    import gradio as gr                              # lazy import: CLI users don't need gradio

    rule_names = list_rule_files()
    first = rule_names[0] if rule_names else None

    with gr.Blocks(title="AutoGrader - C program evaluator") as demo:
        gr.Markdown("# AutoGrader\nRule-driven, reproducible grading of C programs with local-LLM agents (Ollama).")
        with gr.Row():
            # ---------------- left column: inputs ----------------
            with gr.Column(scale=1):
                src = gr.File(label="Student submission (.c)", file_types=[".c"], type="filepath")
                rule_dd = gr.Dropdown(choices=rule_names, value=first, label="Rule set (config/rules_*.json)")
                rules_box = gr.Code(value=load_rules_text(first), language="json", lines=18,
                                    label="Rules (editable - weights, thresholds, LLM settings)")
                with gr.Row():
                    use_llm = gr.Checkbox(value=True, label="Use Ollama (LLM agents)")
                    model = gr.Textbox(label="Ollama model override", placeholder="e.g. llama3.1:8b")
                with gr.Row():
                    check_btn = gr.Button("Check Ollama")
                    run_btn = gr.Button("Grade", variant="primary")
                status = gr.Textbox(label="Status / progress log", lines=6, interactive=False)
            # ---------------- right column: results ----------------
            with gr.Column(scale=2):
                headline = gr.Markdown()
                table = gr.Dataframe(headers=["Metric", "Weight", "Points", "Feedback"], interactive=False,
                                     wrap=True, label="Per-metric results")
                text_out = gr.Textbox(label="Student report", lines=26, interactive=False)
                with gr.Accordion("Full JSON report (machine-readable evidence)", open=False):
                    json_out = gr.JSON()
                files = gr.File(label="Download reports (.json / .txt)", file_count="multiple")

        # ---------------- events ----------------
        rule_dd.change(load_rules_text, inputs=rule_dd, outputs=rules_box)
        check_btn.click(check_ollama, inputs=model, outputs=status)
        run_btn.click(run_grading, inputs=[src, rules_box, model, use_llm],
                      outputs=[headline, table, text_out, json_out, files, status])
    return demo


def launch():
    """Start the web server (host/port can be set with AUTOGRADER_HOST / AUTOGRADER_PORT)."""
    build_ui().queue().launch(server_name=os.environ.get("AUTOGRADER_HOST", "127.0.0.1"),
                              server_port=int(os.environ.get("AUTOGRADER_PORT", "7860")))


if __name__ == "__main__":
    launch()
