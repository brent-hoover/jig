"""Derive the actual dependency graph from code.

Bones stub: the static-analysis approach (grimp vs. an ``ast`` walk) is the
subject of Spike 2 (#202), and the real implementation lands in MVP — which then
dogfoods drift detection on Jig's own codebase. For now this returns an empty
graph so the contract is pinned and downstream ``diff`` is exercisable.
"""

from __future__ import annotations

from pathlib import Path

from jig.engines.reconciliation.graph import DependencyGraph


def derive_actual_graph(_code_path: Path) -> DependencyGraph:
    """Build the actual module dependency graph rooted at ``_code_path``.

    Bones: returns an empty graph (Spike 2 picks the tool; MVP implements). The
    parameter is intentionally unused for now — hence the ``_`` prefix.
    """
    return DependencyGraph.of()
