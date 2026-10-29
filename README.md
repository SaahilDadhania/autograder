# AutoGrader - rule-driven evaluator for C programs

An agentic grader for student C submissions. A **coordinator agent** compiles and smoke-tests the
program, then dispatches **specialist analyzers** (each one metric), combines them with the
instructor's **weights from a JSON rules file**, and returns one reproducible grade plus feedback
(failing input, expected output, explanation). A **local LLM (Ollama)** acts as the readability
evaluator, counter-test generator, failure explainer and summary writer.

No RAG / LangChain / vector DB: the rules and tests are plain files.

## 1. Install (Ubuntu)

```bash
sudo apt update && sudo apt install -y build-essential cppcheck valgrind   # gcc is mandatory, others optional
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b                       # any model; set it in the rules file "llm" block

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Run

```bash
python main.py --submission submissions/add.c --rules config/rules_add.json     # one file
python main.py --batch submissions/ --rules config/rules_add.json               # whole folder
python main.py --submission submissions/add.c --rules config/rules_add.json --no-llm   # without Ollama
python app.py                                                                   # web UI on :7860
```

Reports are written to `reports/<name>.json` (machine) and `reports/<name>.txt` (student).
`sample_reports_no_llm/` holds example output produced with `--no-llm`; run with Ollama for the real thing.

No GPU / no model yet? `python tools/mock_ollama.py` starts a fake Ollama so you can test the LLM code paths.

## 3. Architecture

```
main.py / app.py  (CLI / Gradio)
      |
      v
agent/coordinator.py  --- the MAIN AGENT
      |  1. load + validate rules (agent/rules.py)  ->  rules hash
      |  2. FIRST PASS: gcc compile check, sandboxed smoke run, static sanity
      |       (compile fails / infinite loop -> execution analyzers skipped, PARTIAL report)
      |  3. DISPATCH analyzers named in the rules (thread pool; timing analyzer runs alone)
      |  4. COLLATE: weighted grade, failing inputs (+LLM "WHY"), flags, suggestions, summary
      v
agent/analyzers/            one file per metric (all share the Analyzer interface in base.py)
   io_tests              correctness      hidden tests, + CPU/memory profile
   compiler_warnings     syntax           gcc -Wall -Wextra ...
   static_analysis       structure        cyclomatic complexity, function length, cppcheck
   function_split        modularity       used helper functions
   edge_case_runner      logical          named edge cases vs reference solution + LLM counter-tests
   complexity_estimator  complexity       timing curve -> O(1)/O(n)/...
   sanitizer_run         memory_safety    ASan + UBSan + LeakSanitizer (+ optional valgrind)
   llm_review            readability      Ollama rubric review blended with a heuristic
   similarity_flag       originality      token-shingle similarity + advisory AI-style signals
agent/sandbox.py          limits (time, CPU, memory, output, processes), process-group kill
agent/llm_client.py       Ollama HTTP client + on-disk answer cache
agent/llm_agents.py       counter-test generator, failure explainer, summary writer
agent/prompts.py          ALL system prompts (RCAFT style) - tune agent behaviour here
agent/report.py           JSON + text report writers
```

## 4. The rules file (`config/rules_add.json`)

Instructors edit this file (or the editor box in the web UI) - never the code.

| Key | Meaning |
|---|---|
| `problem`, `problem_statement` | name + text given to the LLM agents |
| `time_limit_s`, `memory_limit_mb` | sandbox limits per run |
| `reference_solution` | correct program used for expected outputs of edge cases / counter-tests |
| `llm` | `model`, `temperature`, `seed`, `num_ctx`, `host`, `use_cache`, ... |
| `metrics[]` | `name`, `analyzer`, `weight` (should sum to 1.0), `params` for that analyzer |

Tests live in `tests/<problem>/NN.in` + `NN.out`. Edge cases are named in `params.cases`
(built-ins in `agent/edge_cases.py`, or your own via `params.custom_cases`).

## 5. Reproducibility ("same submission + same rules = same grade")

* every analyzer except the LLM ones is deterministic (fixed seeds, fixed flags, median timings);
* LLM calls use `temperature 0`, `seed 42`, `top_k 1`, **and** every answer is cached in `.cache/llm/`
  keyed by (model, options, prompts) - re-grading returns the identical text even if the model would vary;
* LLM counter-tests are *reported only* by default (`llm_cases_affect_grade: false`);
* the report stores the rules hash and tool versions (gcc, cppcheck, ollama, model).

For official marks keep the `.cache/` folder (or set `use_cache` true and never change the model).

## 6. Adding a new metric

1. `agent/analyzers/my_metric.py`: subclass `Analyzer`, set `name`, implement `run(ctx, params) -> AnalyzerResult`, decorate with `@register`.
2. Add `my_metric` to the import line in `agent/analyzers/__init__.py`.
3. Use `{"name": "...", "analyzer": "my_metric", "weight": 0.1, "params": {...}}` in the rules.

## 7. Security note

The sandbox uses resource limits and process-group kills, which stops infinite loops, memory
hogs, output floods and fork bombs. It is **not** a jail: a malicious program could still read
readable files or open network connections. For real classes run the whole agent inside Docker
(`--network none`, read-only mounts) or wrap execution with `bubblewrap`/`nsjail`.

## 8. Known limitations (also in every report)

Tests cannot prove correctness; timing-based complexity is noisy for small sizes; similarity /
AI-copy flags need human review; LLM readability scores are subjective. Details with concrete
examples: `config/usefulness_limitations.json`.
