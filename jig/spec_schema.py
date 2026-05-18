"""Pydantic schema for the structured project spec
(``.jig/spec/project.structured.yaml``).

Owned by the spec-generator agent — humans do NOT edit the structured
form directly. The brief (``docs/brief.md``) is the source of
truth for content and IDs; this module validates the projection
spec-gen produces from the brief.

See ``docs/project-spec-schema/design.md`` for the full design.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Kebab-case slug: starts with letter or digit, ends with letter or digit,
# interior may contain hyphens. Single-char slugs (e.g. "a") are allowed.
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _kebab_slug(value: str, field: str = "id") -> str:
    if not _SLUG_RE.match(value):
        raise ValueError(
            f"{field} must be kebab-case (^[a-z0-9]([a-z0-9-]*[a-z0-9])?$), got {value!r}"
        )
    return value


class CapabilityState(str, Enum):
    BACKLOG = "backlog"
    PLANNED = "planned"
    PLANNED_UNCOMMITTED = "planned_uncommitted"
    IN_PROGRESS = "in_progress"
    BUILT = "built"
    ARCHIVED = "archived"


class UserStory(BaseModel):
    """Optional WHO + WHAT + WHY framing for a capability.

    Field name ``as_`` because ``as`` is a Python keyword; YAML/dict
    surface uses ``as`` via the alias.
    """

    as_: str = Field(alias="as")
    want: str
    benefit: str

    model_config = {"populate_by_name": True}


class Behavior(BaseModel):
    id: str
    description: str
    examples: list[str] = []
    # Required; no default — min_length=1 enforces ≥1 AC.
    acceptance_criteria: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return _kebab_slug(v)


class NonGoal(BaseModel):
    id: str
    text: str
    rationale: str = ""
    aliases: list[str] = []

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return _kebab_slug(v)

    @field_validator("aliases")
    @classmethod
    def _validate_aliases(cls, v: list[str]) -> list[str]:
        return [_kebab_slug(a, "alias") for a in v]


_AC_REQUIRED_STATES = {
    CapabilityState.PLANNED,
    CapabilityState.IN_PROGRESS,
    CapabilityState.BUILT,
}


class GivenWhenThen(BaseModel):
    """Structured example for a capability or ticket.

    Provides concrete scenarios (given a precondition, when an action occurs,
    then an outcome is expected). Reviewers and the sim driver can consume
    these directly instead of parsing prose AC.
    """

    model_config = ConfigDict(extra="forbid")

    given: str = Field(..., min_length=1)
    when: str = Field(..., min_length=1)
    then: str = Field(..., min_length=1)


class DoneEnoughBlock(BaseModel):
    """Layer-scoped definition of what 'done enough' means for a capability.

    The PM reads these when planning tickets to populate ``done_when``
    for each layer. Having three blocks (bones / mvp / final) makes the
    layering decision explicit and queryable rather than buried in prose.
    """

    model_config = ConfigDict(extra="forbid")

    layer: Literal["bones", "mvp", "final"]
    criteria: list[str] = Field(..., min_length=1)


class Capability(BaseModel):
    id: str
    title: str
    state: CapabilityState
    summary: str = ""
    user_story: UserStory | None = None
    behaviors: list[Behavior] = []
    acceptance_criteria: list[str] = []  # capability-level, used when no behaviors
    examples: list[GivenWhenThen] = []
    done_enough: list[DoneEnoughBlock] = []
    excluded: list[str] = []
    open_questions: list[str] = []
    tickets: list[str] = []  # rebuilt by spec-gen from ticket store
    aliases: list[str] = []
    created_at: datetime
    last_updated: datetime
    state_changed_at: datetime

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return _kebab_slug(v)

    @field_validator("aliases")
    @classmethod
    def _validate_aliases(cls, v: list[str]) -> list[str]:
        return [_kebab_slug(a, "alias") for a in v]

    @model_validator(mode="after")
    def _validate_ac_for_state(self) -> "Capability":
        """For elaborated states, AC must exist somewhere — capability-level
        OR every behavior has its own (Pydantic enforces ≥1 per behavior
        already)."""
        if self.state not in _AC_REQUIRED_STATES:
            return self
        if self.acceptance_criteria:
            return self
        if self.behaviors:
            # Each behavior already has min_length=1 via Behavior schema.
            return self
        raise ValueError(
            f"capability {self.id!r} (state={self.state.value}) requires at "
            "least one acceptance criterion, either capability-level or via "
            "behaviors"
        )


class StructuredSpec(BaseModel):
    name: str
    summary: str
    capabilities: list[Capability] = []
    non_goals: list[NonGoal] = []
    generated_at: datetime
    spec_version: int = 1

    @field_validator("spec_version")
    @classmethod
    def _validate_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported spec_version {v} (this code is v1)")
        return v

    def capability_by_id_or_alias(self, key: str) -> Capability | None:
        """Look up a capability by its id or any alias. None if no match."""
        for c in self.capabilities:
            if c.id == key or key in c.aliases:
                return c
        return None

    def non_goal_by_id_or_alias(self, key: str) -> NonGoal | None:
        """Look up a non-goal by its id or any alias. None if no match."""
        for ng in self.non_goals:
            if ng.id == key or key in ng.aliases:
                return ng
        return None
