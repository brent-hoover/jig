"""PO output schemas — L0 Project + L2 SuitesIndex + L3 SuiteBriefStructured.

L1 (discovery) schemas are out of bones scope; they land with the L1
PO conversation work in Track B.

L3 reuses ``jig.spec_schema.StructuredSpec`` shape — the structured
projection of a single suite brief is the same shape as the v1 monolithic
spec, just scoped to one suite. We re-export it here for discoverability;
suite-scope and federation-level differences live in the file layout, not
the schema.

``SuitesIndex`` is the L2 artifact that the L3 PO READS to know its
suite's capability allowlist. The L2 PO authoring side is out of bones
scope (synthetic operator hand-writes ``suites.yaml``); the schema lives
here so L3 can validate against it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from jig.spec_schema import StructuredSpec

__all__ = [
    "Project",
    "ProductNonGoal",
    "Suite",
    "SuitesIndex",
    "StructuredSpec",
]


class ProductNonGoal(BaseModel):
    """A project-level product non-goal captured in the L0 pitch.

    Re-used at L2 for ``crosscutting_non_goals`` — same shape, different
    scope (the suites-spanning non-goals an operator wants tracked
    alongside the suite list).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="kebab-case stable id")
    text: str = Field(..., min_length=1)
    rationale: str | None = None


class Project(BaseModel):
    """L0 — the project pitch.

    Written by the L0 PO in 3-5 turns. Captures pitch + problem + audience +
    product-level non-goals. Does not mention features, suites, or
    capabilities — those land at L1/L2/L3.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 2
    name: str = Field(..., min_length=1)
    pitch: str = Field(..., min_length=1, description="One-sentence pitch.")
    problem: str = Field(
        ...,
        min_length=1,
        description=(
            "One paragraph: what's broken in the world this fixes, and why "
            "it matters to the audience."
        ),
    )
    audience: str = Field(
        ...,
        min_length=1,
        description="One paragraph: who uses this and what they're trying to do.",
    )
    non_goals: list[ProductNonGoal] = Field(default_factory=list)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Suite(BaseModel):
    """One suite entry in ``.jig/spec/suites.yaml``.

    The L2 PO authors this; the L3 PO reads it to scope a single suite's
    brief. ``capabilities`` is the allowlist — L3 may only elaborate
    capabilities whose ids appear here. Adding a capability not on this
    list is a gap that must go back to L1; for bones the L3 PO simply
    rejects it.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="kebab-case suite id")
    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    capabilities: list[str] = Field(
        default_factory=list,
        description="L1 capability ids assigned to this suite.",
    )


class SuitesIndex(BaseModel):
    """L2 — the suite organization for a project.

    Authoring is out of bones scope. The synthetic operator hand-writes
    a minimal ``suites.yaml`` and the L3 PO reads it via
    ``jig.spec_loader.load_suites_index`` to look up its suite entry +
    capability allowlist.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 2
    suites: list[Suite] = Field(default_factory=list)
    crosscutting_non_goals: list[ProductNonGoal] = Field(default_factory=list)

    def suite_by_id(self, suite_id: str) -> Suite | None:
        """Return the suite with this id, or ``None``."""
        for s in self.suites:
            if s.id == suite_id:
                return s
        return None
