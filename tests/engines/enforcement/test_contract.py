"""Enforcement bones — the Review contract (Epic 5, task 2).

``Review(diff, invariant_context) -> list[Finding]`` is the headless-invocable
seam Build calls to enforce the invariants on a diff. Findings are the canonical
``jig.model.Finding`` (Epic 1). Bones defines the contract + a mechanical stub;
MVP runs real boundary/vocabulary checks and wires Build to call it.
"""

from __future__ import annotations

from jig.engines.enforcement import InvariantContext, MechanicalReview, Review
from jig.model import Finding


def test_review_is_a_runtime_checkable_protocol() -> None:
    assert isinstance(MechanicalReview(), Review)

    class _NotAReview:
        pass

    assert not isinstance(_NotAReview(), Review)


def test_mechanical_review_returns_findings() -> None:
    findings = MechanicalReview()(diff="", invariant_context=InvariantContext())
    assert findings == []  # bones stub — MVP returns real boundary findings


def test_a_review_yields_core_model_findings() -> None:
    class _FakeReview:
        def __call__(
            self, diff: str, invariant_context: InvariantContext
        ) -> list[Finding]:
            return [Finding(invariant="containment", message="boundary breach")]

    assert isinstance(_FakeReview(), Review)
    (finding,) = _FakeReview()(diff="x", invariant_context=InvariantContext())
    assert isinstance(finding, Finding)
    assert finding.invariant == "containment"
