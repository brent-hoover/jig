"""Reconciliation bones — derive actual structure from code (Epic 7, task 2).

``derive_actual_graph(code_path) -> DependencyGraph`` is the Bones stub: the real
static-analysis implementation (grimp / ast) is chosen by Spike 2 (#202) and
landed in MVP. Bones pins the contract — it returns an empty graph for now.
"""

from __future__ import annotations

from pathlib import Path

from jig.engines.reconciliation import DependencyGraph, derive_actual_graph


def test_derive_returns_a_dependency_graph() -> None:
    graph = derive_actual_graph(Path("."))
    assert isinstance(graph, DependencyGraph)


def test_derive_is_an_empty_bones_stub() -> None:
    graph = derive_actual_graph(Path("."))
    assert graph.nodes == frozenset()
    assert graph.edges == frozenset()
