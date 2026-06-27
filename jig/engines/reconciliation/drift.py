"""DriftReport + diff — declared model vs. actual code structure.

``diff`` is a pure set-difference over two ``DependencyGraph``s. "Undeclared"
means the code has something the model doesn't (a containment-style violation);
"missing" means the model declares something the code doesn't have (a stale
declaration). MVP surfaces a non-empty report as work (tickets) via Build.
"""

from __future__ import annotations

from dataclasses import dataclass

from jig.engines.reconciliation.graph import DependencyGraph


@dataclass(frozen=True)
class DriftReport:
    """The difference between a declared graph and the actual (derived) graph."""

    undeclared_edges: frozenset[tuple[str, str]]
    missing_edges: frozenset[tuple[str, str]]
    undeclared_nodes: frozenset[str]
    missing_nodes: frozenset[str]

    @property
    def has_drift(self) -> bool:
        return bool(
            self.undeclared_edges
            or self.missing_edges
            or self.undeclared_nodes
            or self.missing_nodes
        )


def diff(declared: DependencyGraph, actual: DependencyGraph) -> DriftReport:
    """Pure structural diff: what the code has that the model doesn't declare,
    and what the model declares that the code lacks."""
    return DriftReport(
        undeclared_edges=actual.edges - declared.edges,
        missing_edges=declared.edges - actual.edges,
        undeclared_nodes=actual.nodes - declared.nodes,
        missing_nodes=declared.nodes - actual.nodes,
    )
