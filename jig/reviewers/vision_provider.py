"""Injectable vision-provider Protocol for visual-compliance Final scope.

Per ``docs/v2.0/visual-design/design.md`` §"Visual compliance reviewer" and
``docs/v2.0/implementation/v2-plan.md`` §"Final scope" Track D row, the
Final-layer visual reviewer compares an implementation screenshot to the
authored wireframe via a vision-capable LLM.

The provider is a **Protocol seam** rather than a hard-coded Claude /
GPT vision call: production wires its preferred vision LLM, tests +
mock-mode scenarios wire ``StubVisionProvider``. Keeping the seam
out of the reviewer module means the reviewer stays unit-testable
without spinning up a vision API.

Vision is the *only* LLM-shaped dependency in our visual / a11y /
responsive surface — the accessibility + responsive reviewers stay
fully mechanical. The Protocol's single async entry point keeps the
boundary narrow: pass two image byte-streams + textual context, get
a structured ``VisionDiffResult`` back.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "StubVisionProvider",
    "VisionDiffResult",
    "VisionProviderProtocol",
    "VisualDifference",
]


class VisualDifference(BaseModel):
    """One difference the vision provider spotted between reference + candidate.

    ``kind`` is constrained to a small enumerated set so downstream
    comment formatting + analytics aggregation stay predictable —
    free-form prose lives on ``description``. ``location_hint`` is
    optional because vision providers can't always pinpoint a region;
    when populated it's a short human phrase ("top-right CTA",
    "footer column 2") rather than coordinates.

    ``severity`` mirrors the ReviewerComment severity tiers so the
    reviewer can map differences directly into comments without
    re-classifying.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    kind: Literal[
        "layout-shift",
        "color-mismatch",
        "missing-component",
        "extra-component",
        "spacing-deviation",
    ]
    description: str = Field(..., min_length=1)
    location_hint: str | None = None
    severity: Literal["critical", "important", "notable"]


class VisionDiffResult(BaseModel):
    """Structured diff result returned by the vision provider.

    Empty ``differences`` means the implementation matches the
    wireframe within the provider's tolerance — no comments emitted.
    """

    model_config = ConfigDict(extra="forbid")

    differences: list[VisualDifference] = Field(default_factory=list)


class VisionProviderProtocol(Protocol):
    """Async seam for the vision LLM.

    Implementations take two image byte-streams (wireframe reference,
    implementation candidate) plus a free-form ``context`` string the
    reviewer uses to pass screen-id / ticket info into the prompt, and
    return a structured ``VisionDiffResult``.

    Production wires the operator's preferred provider (Claude Vision,
    OpenAI GPT-4V, etc.) outside of jig's tree. Tests + mock scenarios
    wire ``StubVisionProvider`` — see below.
    """

    async def compare_images(
        self,
        reference: bytes,
        candidate: bytes,
        context: str,
    ) -> VisionDiffResult:
        """Compare two images; return structured differences."""
        ...


class StubVisionProvider:
    """Deterministic test double — returns a pre-canned diff result.

    Tests + mock-mode scenarios construct one with the diff result they
    want the reviewer to see, then inject it into
    ``FullVisualComplianceReviewer.review_full``. The stub records every
    call so tests can assert on invocation count + the context string
    the reviewer threaded through.

    Not a Pydantic model — it's a behavioral stub, not a schema.
    """

    def __init__(self, result: VisionDiffResult | None = None) -> None:
        self._result = result or VisionDiffResult()
        self.calls: list[dict[str, object]] = []

    async def compare_images(
        self,
        reference: bytes,
        candidate: bytes,
        context: str,
    ) -> VisionDiffResult:
        self.calls.append(
            {
                "reference_len": len(reference),
                "candidate_len": len(candidate),
                "context": context,
            }
        )
        return self._result
