
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


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
    
    score: float = 0.0
    feedback: str = ""
    evidence: dict = field(default_factory=dict)
    failures: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    skipped: bool = False


@dataclass
class AnalysisContext:
    
    source_path: Path
    source_text: str
    workdir: Path
    rules: Any
    llm: Any
    binary: Optional[Path] = None
    reference_binary: Optional[Path] = None
    shared_inputs: list = field(default_factory=list)
    execution_allowed: bool = True
