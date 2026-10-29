"""
agent/analyzers/base.py
========================
The contract every analyzer ("expert agent") must follow, plus the registry the
coordinator uses to find analyzers by the name written in the rules file.

HOW TO ADD A NEW METRIC (3 steps):
    1. Create agent/analyzers/my_metric.py with a class that extends `Analyzer`,
       sets `name = "my_metric"` and implements run(ctx, params).
    2. Decorate the class with @register.
    3. Import the module in agent/analyzers/__init__.py.
    Then the instructor can use {"analyzer": "my_metric", ...} in the rules file.
"""
from __future__ import annotations

from typing import Dict, Type

from ..common import AnalysisContext, AnalyzerResult

REGISTRY: Dict[str, Type["Analyzer"]] = {}


class Analyzer:
    # Name used in the rules file ("analyzer": "<name>")
    name: str = "base"
    # True -> needs the compiled program to RUN; skipped if it cannot execute
    requires_execution: bool = False
    # False -> must run alone (e.g. timing measurements get disturbed by other work)
    parallel_safe: bool = True

    def run(self, ctx: AnalysisContext, params: dict) -> AnalyzerResult:
        """Evaluate one metric.  `params` is this metric's slice of the rules file."""
        raise NotImplementedError


def register(cls):
    """Class decorator: make the analyzer discoverable by name."""
    REGISTRY[cls.name] = cls
    return cls
