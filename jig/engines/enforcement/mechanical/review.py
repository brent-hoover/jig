"""MechanicalReview — the deterministic ``Review`` (boundary + vocabulary).

Bones: a stub that satisfies the ``Review`` contract and returns no findings.
MVP runs the real checks — import deny-lists against declared boundaries
(a violation is a build-blocking finding, not a reviewer judgment) and ontology
term-drift ("one concept, one home") — and maps them to ``Finding``s.
"""

from __future__ import annotations

from jig.engines.enforcement.contract import InvariantContext
from jig.model import Finding


class MechanicalReview:
    """A deterministic ``Review`` over a diff. Bones returns no findings."""

    def __call__(self, diff: str, invariant_context: InvariantContext) -> list[Finding]:
        return []
