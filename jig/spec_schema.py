"""Pydantic schema for the structured project spec
(``.jig/spec/project.structured.yaml``).

Owned by the spec-generator agent — humans do NOT edit the structured
form directly. The brief (``.jig/spec/project.md``) is the source of
truth for content and IDs; this module validates the projection
spec-gen produces from the brief.

See ``docs/project-spec-schema/design.md`` for the full design.
"""
from __future__ import annotations

import re
from datetime import datetime  # noqa: F401 — available for downstream models
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# Kebab-case slug: starts with letter or digit, then letters/digits/hyphens.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _kebab_slug(value: str, field: str = "id") -> str:
    if not _SLUG_RE.match(value):
        raise ValueError(
            f"{field} must be kebab-case (^[a-z0-9][a-z0-9-]*$), got {value!r}"
        )
    return value


class CapabilityState(str, Enum):
    BACKLOG = "backlog"
    PLANNED = "planned"
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
