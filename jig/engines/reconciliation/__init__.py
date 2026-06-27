"""Reconciliation engine — keep the declared model honest against the code.

Derive the **actual** structure from code (``derive_actual_graph``), diff it
against the **declared** model (``diff`` -> ``DriftReport``), and — in MVP —
surface the drift as work via Build. Bones defines the contracts: ``diff`` is
real (a pure graph set-diff); ``derive_actual_graph`` is the Spike-2-gated stub.
"""

from __future__ import annotations

from jig.engines.reconciliation.derive import derive_actual_graph
from jig.engines.reconciliation.drift import DriftReport, diff
from jig.engines.reconciliation.graph import DependencyGraph

__all__ = [
    "DependencyGraph",
    "DriftReport",
    "derive_actual_graph",
    "diff",
]
