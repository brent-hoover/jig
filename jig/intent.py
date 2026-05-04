"""Intent layer — disciplined sequence baked into v2 authored artifacts.

The intent layer replaces the flat ``rationale`` framing with a structured
sequence: problem → simplest_solution → complications_considered. Every
artifact authored by an agent fills the sequence, which forces the agent to
articulate the simplest baseline before earning any complexity.

See ``docs/v2.0/agent-leverage/problem.md`` §1 — every complication in the
proposed solution must be earned by an explicit complication entry.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ComplicationsConsidered(BaseModel):
    """Complications that pushed the artifact past its simplest form.

    The four canonical keys (scale, concurrency, failure_modes,
    cross_cutting) are well-known dimensions every artifact should consider.
    Additional problem-specific complications are accepted via ``extra``.
    A ``None`` value means "considered and does not apply"; an empty string
    means "not yet considered" and is flagged by the intent reviewer.
    """

    model_config = ConfigDict(extra="allow")

    scale: str | None = None
    concurrency: str | None = None
    failure_modes: str | None = None
    cross_cutting: str | None = None


class Intent(BaseModel):
    """Disciplined intent sequence carried by every authored v2 artifact."""

    problem: str = Field(
        ...,
        min_length=1,
        description="What this artifact is solving — concrete, not boilerplate.",
    )
    simplest_solution: str = Field(
        ...,
        min_length=1,
        description=(
            "Most obvious dumb thing that would solve the problem. The "
            "baseline complexity has to clear before any sophistication "
            "is earned."
        ),
    )
    complications_considered: ComplicationsConsidered = Field(
        default_factory=ComplicationsConsidered,
        description=(
            "Each complication explains a step beyond the simplest "
            "solution. None means none apply; the intent reviewer flags "
            "boilerplate."
        ),
    )
