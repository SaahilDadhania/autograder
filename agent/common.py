"""
agent/common.py
================
Small helpers + data classes shared by EVERY module of the AutoGrader.

Nothing in here talks to the compiler, the LLM or the file system rules --
it only defines the "vocabulary" the other modules use to talk to each other:

    AnalyzerResult   -> what every analyzer returns to the coordinator
    AnalysisContext  -> what the coordinator hands to every analyzer
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# Root folder of the project (the folder that contains main.py).
# All relative paths written inside rules files are resolved against this,
# so the agent behaves the same no matter which directory you launch it from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def sha256_text(text: str) -> str:
    """Hex SHA-256 of a string (used for the rules hash and the LLM cache keys)."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_json(obj: Any) -> str:
    """Stable JSON text (sorted keys, fixed separators) -> same input = same hash."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class AnalyzerResult:
    """
    The single thing an analyzer must return.

    score     : float in [0, 1]  (1 = full marks for this metric)
    feedback  : one short human-readable sentence for the student
    evidence  : raw machine-readable details (goes into the JSON report)
    failures  : list of dicts {input, expected, got, status, note, source}
                describing concrete failing inputs (shown in "Where you failed")
    flags     : list of dicts for things a human should look at (e.g. plagiarism)
    skipped   : True when the analyzer was not run (e.g. program never halts)
    """
    score: float = 0.0
    feedback: str = ""
    evidence: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    skipped: bool = False


@dataclass
class AnalysisContext:
    """
    Everything an analyzer may need, prepared ONCE by the coordinator.

    source_path       : path of the student's .c file (original file name kept)
    source_text       : full text of that file
    workdir           : private temp folder analyzers may write into
    rules             : the loaded Rules object (see agent/rules.py)
    llm               : OllamaClient shared by all LLM-using analyzers
    binary            : normal (-O2) compiled executable, or None if compile failed
    reference_binary  : instructor's reference solution compiled, or None
    shared_inputs     : [(label, stdin_text), ...] every known test input; the
                        sanitizer analyzer re-uses them to hunt for memory bugs
    execution_allowed : False if compile failed / program never halts
    """
    source_path: Path
    source_text: str
    workdir: Path
    rules: Any
    llm: Any
    binary: Optional[Path] = None
    reference_binary: Optional[Path] = None
    shared_inputs: list = field(default_factory=list)
    execution_allowed: bool = True
