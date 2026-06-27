"""Reconciliation bones — the model-vs-code diff (Epic 7, task 3).

``diff(declared, actual) -> DriftReport`` is a pure graph set-diff: edges/nodes
the code has but the model doesn't declare (undeclared — a containment-style
violation), and edges/nodes the model declares but the code lacks (missing —
stale declaration). Real and fully implemented in Bones; tested with synthetic
graphs.
"""

from __future__ import annotations

from jig.engines.reconciliation import DependencyGraph, diff


def test_no_drift_when_graphs_match() -> None:
    graph = DependencyGraph.of(nodes=["a", "b"], edges=[("a", "b")])

    report = diff(declared=graph, actual=graph)

    assert not report.has_drift
    assert report.undeclared_edges == frozenset()
    assert report.missing_edges == frozenset()
    assert report.undeclared_nodes == frozenset()
    assert report.missing_nodes == frozenset()


def test_empty_graphs_have_no_drift() -> None:
    report = diff(declared=DependencyGraph.of(), actual=DependencyGraph.of())
    assert not report.has_drift


def test_edge_implied_nodes_do_not_cause_spurious_node_drift() -> None:
    # Neither graph declares nodes explicitly; both are implied by the edge.
    declared = DependencyGraph.of(edges=[("a", "b")])
    actual = DependencyGraph.of(edges=[("a", "b")])

    assert not diff(declared=declared, actual=actual).has_drift


def test_undeclared_edge_is_drift() -> None:
    # The code depends a->b, but the model doesn't allow it.
    declared = DependencyGraph.of(nodes=["a", "b"], edges=[])
    actual = DependencyGraph.of(nodes=["a", "b"], edges=[("a", "b")])

    report = diff(declared=declared, actual=actual)

    assert report.undeclared_edges == frozenset({("a", "b")})
    assert report.missing_edges == frozenset()
    assert report.has_drift


def test_missing_edge_is_drift() -> None:
    # The model declares a->b, but the code doesn't actually use it.
    declared = DependencyGraph.of(nodes=["a", "b"], edges=[("a", "b")])
    actual = DependencyGraph.of(nodes=["a", "b"], edges=[])

    report = diff(declared=declared, actual=actual)

    assert report.missing_edges == frozenset({("a", "b")})
    assert report.undeclared_edges == frozenset()
    assert report.has_drift


def test_node_drift_in_both_directions() -> None:
    declared = DependencyGraph.of(nodes=["a", "c"])
    actual = DependencyGraph.of(nodes=["a", "b"])

    report = diff(declared=declared, actual=actual)

    assert report.undeclared_nodes == frozenset({"b"})  # code has it, model doesn't
    assert report.missing_nodes == frozenset({"c"})  # model has it, code doesn't
