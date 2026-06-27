"""DependencyGraph — modules and their dependency edges.

The same shape represents both the **declared** structure (from the architecture
model's boundaries/dependencies) and the **actual** structure (derived from
code). Reconciliation diffs the two. An edge ``(a, b)`` means "module ``a``
depends on module ``b``".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class DependencyGraph:
    """An immutable module dependency graph."""

    nodes: frozenset[str]
    edges: frozenset[tuple[str, str]]

    @classmethod
    def of(
        cls,
        *,
        nodes: Iterable[str] = (),
        edges: Iterable[tuple[str, str]] = (),
    ) -> DependencyGraph:
        """Build from any iterables, normalizing to frozensets. Edge endpoints
        are also added to ``nodes`` so the node set is always complete."""
        edge_set = frozenset(edges)
        node_set = frozenset(nodes) | {n for edge in edge_set for n in edge}
        return cls(nodes=node_set, edges=edge_set)
