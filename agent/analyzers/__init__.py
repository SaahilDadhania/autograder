"""
agent/analyzers/__init__.py
============================
Importing this package imports every analyzer module, which makes each one
register itself in REGISTRY (see base.py).  To add a metric, add one import line.
"""
from .base import REGISTRY, Analyzer, register          # noqa: F401
from . import (io_tests, compiler_warnings, static_analysis, function_split,   # noqa: F401
               edge_case_runner, complexity_estimator, sanitizer_run,
               llm_review, similarity_flag)
