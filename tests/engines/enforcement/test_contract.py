"""Enforcement bones — the Review contract (Epic 5, task 2).

``Review(diff, invariant_context) -> list[Finding]`` is the headless-invocable
seam Build calls to enforce the invariants on a diff. Findings are the canonical
``jig.model.Finding`` (Epic 1). Bones defines the contract + a mechanical stub;
MVP runs real boundary/vocabulary checks and wires Build to call it.
"""

from __future__ import annotations

import inspect

from jig.engines.enforcement import InvariantContext, MechanicalReview, Review
from jig.model import Finding


def test_mechanical_review_satisfies_the_async_review_contract() -> None:
    review = MechanicalReview()
    # Structurally a Review (has __call__)...
    assert isinstance(review, Review)
    # ...and async — the property the runtime_checkable Protocol can NOT enforce
    # (see test below), so it's asserted explicitly.
    assert inspect.iscoroutinefunction(review.__call__)


def test_runtime_protocol_check_only_verifies_callability() -> None:
    # Documents a Python limitation, not a guarantee: isinstance(x, Review) only
    # checks that __call__ exists, not that it's a coroutine. A SYNC callable
    # passes the Protocol check even though `await x(...)` would raise at the
    # call site — so async-ness must be verified separately (test above).
    def sync_impl(diff: str, invariant_context: InvariantContext) -> list[Finding]:
        return []

    assert isinstance(sync_impl, Review)  # passes despite being sync
    assert not inspect.iscoroutinefunction(sync_impl)

    # A non-callable is genuinely not a Review.
    assert not isinstance(object(), Review)


async def test_mechanical_review_returns_findings() -> None:
    findings = await MechanicalReview()(diff="", invariant_context=InvariantContext())
    assert findings == []  # bones stub — MVP returns real boundary findings


async def test_a_review_yields_core_model_findings() -> None:
    class _FakeReview:
        async def __call__(
            self, diff: str, invariant_context: InvariantContext
        ) -> list[Finding]:
            return [Finding(invariant="containment", message="boundary breach")]

    assert isinstance(_FakeReview(), Review)
    (finding,) = await _FakeReview()(diff="x", invariant_context=InvariantContext())
    assert isinstance(finding, Finding)
    assert finding.invariant == "containment"
