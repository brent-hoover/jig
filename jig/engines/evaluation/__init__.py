"""Evaluation engine — drive Build + the review loop headless on fixtures.

The bones acceptance gate: ``eval_run`` composes the Agent Runtime
(``FixtureRunAgent``), the Build engine (``decide``/dispatch/coordinator), and
Enforcement (``Review``) into a code-pipeline that runs without the daemon, TUI,
Orchestrator god-object, or MCP server. MVP adds labeled-diff corpora,
convergence metrics, and the record/replay agent (Spike 3).
"""

from __future__ import annotations

from jig.engines.evaluation.harness import (
    BuildConfig,
    EvalResult,
    Fixture,
    eval_run,
)

__all__ = [
    "BuildConfig",
    "EvalResult",
    "Fixture",
    "eval_run",
]
