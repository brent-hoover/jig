"""PM output schemas — build-plan.yaml.

The build plan is the PM's living artifact: epics × layers × tickets, with
ordering rule, stalled tickets, and open questions. Tickets in the plan
reference the live ticket store (``jig.ticket.Ticket``) by id; the plan
holds the *organization*, not the ticket bodies.

Bones scope: ``BuildPlan``, ``Epic``, ``LayerStatus``, plus the v2 ticket
extension fields exposed via ``jig.ticket.Ticket`` (``suite_id``,
``module_id``, ``capability_ids``, ``epic_id``, ``layer``, ``dev_tier``,
``reviewer_set``, ``context_hints``, ``risks_addressed``, ``done_when``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from jig.intent import Intent
from jig.schemas.arch import OpenQuestion

__all__ = [
    "BuildPlan",
    "Epic",
    "EpicLayers",
    "LayerName",
    "LayerStatus",
    "LayerStatusEnum",
    "OrderingRule",
    "StalledTicket",
]


class LayerName(str, Enum):
    BONES = "bones"
    MVP = "mvp"
    FINAL = "final"


class LayerStatusEnum(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"


class OrderingRule(str, Enum):
    """How layers are sequenced across epics.

    ``bones_first`` — every epic completes its bones layer before any epic
    starts MVP. The default; biases toward end-to-end demonstrability.
    ``per_epic`` — each epic walks bones → mvp → final independently.
    """

    BONES_FIRST = "bones_first"
    PER_EPIC = "per_epic"


class LayerStatus(BaseModel):
    """One layer (bones / mvp / final) within an epic."""

    model_config = ConfigDict(extra="forbid")

    status: LayerStatusEnum = LayerStatusEnum.NOT_STARTED
    tickets: list[str] = Field(
        default_factory=list,
        description="Ticket ids in the live store. Bodies live there, not here.",
    )


class EpicLayers(BaseModel):
    """The three completeness layers for an epic.

    Each layer is optional in a sense — an epic might be all-bones during
    early discovery — but the schema slot exists so promotions don't
    require a structural change.
    """

    model_config = ConfigDict(extra="forbid")

    bones: LayerStatus = Field(default_factory=LayerStatus)
    mvp: LayerStatus = Field(default_factory=LayerStatus)
    final: LayerStatus = Field(default_factory=LayerStatus)


class Epic(BaseModel):
    """A build-plan epic — one chunk of work that lives across layers."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    suite: str = Field(..., min_length=1)
    modules: list[str] = Field(default_factory=list)
    layers: EpicLayers = Field(default_factory=EpicLayers)
    risks_addressed: list[str] = Field(default_factory=list)
    intent: Intent = Field(
        ...,
        description="Why this epic exists; the simplest carve-up that solves the problem.",
    )


class StalledTicket(BaseModel):
    """A ticket the Coordinator paused; surfaces in the operator's view."""

    model_config = ConfigDict(extra="forbid")

    ticket: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    blocked_since: datetime
    spike: str | None = None


class BuildPlan(BaseModel):
    """The PM's living build plan. Lives at .jig/plan/build-plan.yaml."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    project: str = Field(..., min_length=1)
    revision: int = Field(default=1, ge=1)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_revised: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    ordering_rule: OrderingRule = OrderingRule.BONES_FIRST
    epics: list[Epic] = Field(default_factory=list)
    stalled: list[StalledTicket] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
