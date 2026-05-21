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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jig.intent import Intent
from jig.schemas._validators import validate_kebab_id, validate_tz_aware
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
    acceptance_criteria: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Concrete behaviors the epic must satisfy before it is "
            "considered done. Each bullet is rendered into the ``## "
            "Acceptance criteria`` section of every Ticket the "
            "Coordinator materializes from this epic, so downstream "
            "reviewers (test-adequacy in particular) can anchor "
            "findings against a specific AC item rather than the "
            "epic's free-form intent prose. Authoring guidance lives "
            "in the planner-pm role prompt."
        ),
    )

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "Epic.id")

    @field_validator("suite")
    @classmethod
    def _kebab_suite(cls, v: str) -> str:
        return validate_kebab_id(v, "Epic.suite")

    @field_validator("acceptance_criteria")
    @classmethod
    def _non_empty_bullets(cls, v: list[str]) -> list[str]:
        """Each AC bullet must be non-empty after stripping whitespace.

        A blank or whitespace-only bullet would render as ``- \\n``
        which fails the Ticket model's AC bullet check (requires
        ``\\S`` after the marker) — better to reject at the Epic
        layer with a precise error than to let it crash later in
        Coordinator materialization.
        """
        cleaned: list[str] = []
        for i, bullet in enumerate(v):
            stripped = bullet.strip()
            if not stripped:
                raise ValueError(
                    f"Epic.acceptance_criteria[{i}] is empty or whitespace-only; "
                    "every bullet must describe a concrete behaviour."
                )
            cleaned.append(stripped)
        return cleaned


class StalledTicket(BaseModel):
    """A ticket the Coordinator paused; surfaces in the operator's view."""

    model_config = ConfigDict(extra="forbid")

    ticket: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    blocked_since: datetime
    spike: str | None = None

    @field_validator("blocked_since")
    @classmethod
    def _tz_blocked_since(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "StalledTicket.blocked_since")


class BuildPlan(BaseModel):
    """The PM's living build plan. Lives at .jig/plan/build-plan.yaml."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    project: str = Field(..., min_length=1)
    revision: int = Field(default=1, ge=1)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_revised: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ordering_rule: OrderingRule = OrderingRule.BONES_FIRST
    epics: list[Epic] = Field(default_factory=list)
    stalled: list[StalledTicket] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)

    @field_validator("generated_at", "last_revised")
    @classmethod
    def _tz_timestamps(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "BuildPlan.<timestamp>")

    @model_validator(mode="after")
    def _enforce_uniqueness(self) -> BuildPlan:
        """Two-axis uniqueness inside the build plan:

        * Epic ids unique across the plan (Coordinator dispatch +
          reviewer federation key off ``Epic.id``).
        * Ticket ids unique across all epics × all layers — a ticket
          must appear in exactly one (epic, layer) slot. Duplicates
          across layers silently double-dispatch the ticket; duplicates
          across epics break the layer-status views.
        """
        # Epic-id uniqueness.
        epic_ids: set[str] = set()
        epic_dupes: set[str] = set()
        for epic in self.epics:
            if epic.id in epic_ids:
                epic_dupes.add(epic.id)
            else:
                epic_ids.add(epic.id)
        if epic_dupes:
            raise ValueError(
                f"BuildPlan.epics: duplicate epic id(s) "
                f"{sorted(epic_dupes)!r}. Coordinator dispatch keys off "
                f"Epic.id; duplicates break ticket/layer routing."
            )
        # Cross-layer ticket-id uniqueness.
        ticket_seen: dict[str, str] = {}  # ticket_id -> "epic.layer" anchor
        ticket_dupes: dict[str, list[str]] = {}
        for epic in self.epics:
            for layer_name in ("bones", "mvp", "final"):
                layer_obj = getattr(epic.layers, layer_name)
                for tid in layer_obj.tickets:
                    anchor = f"{epic.id}.{layer_name}"
                    if tid in ticket_seen:
                        ticket_dupes.setdefault(tid, [ticket_seen[tid]]).append(anchor)
                    else:
                        ticket_seen[tid] = anchor
        if ticket_dupes:
            offenders = ", ".join(
                f"{tid!r} in {locations!r}"
                for tid, locations in sorted(ticket_dupes.items())
            )
            raise ValueError(
                f"BuildPlan: ticket id(s) appear in multiple (epic, "
                f"layer) slots: {offenders}. A ticket belongs to exactly "
                f"one slot — duplicates double-dispatch and break the "
                f"layer-status views."
            )
        return self
