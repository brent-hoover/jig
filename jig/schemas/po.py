"""PO output schemas — L0 Project + L3 SuiteBriefStructured.

L1 (discovery) and L2 (suites.yaml) schemas are out of bones scope; they
land with the L1/L2 PO conversation work in Track B.

L3 reuses ``jig.spec_schema.StructuredSpec`` shape — the structured
projection of a single suite brief is the same shape as the v1 monolithic
spec, just scoped to one suite. We re-export it here for discoverability;
suite-scope and federation-level differences live in the file layout, not
the schema.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from jig.spec_schema import StructuredSpec

__all__ = [
    "Project",
    "ProductNonGoal",
    "StructuredSpec",
]


class ProductNonGoal(BaseModel):
    """A project-level product non-goal captured in the L0 pitch."""

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
