#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pydantic>=2.6",
#     "pyyaml>=6",
# ]
# ///
"""Standalone validator for `.jig/spec/project.structured.yaml`.

Mirrors the schema in jig/spec_schema.py but has no jig imports — drop this
script anywhere and run it with `uv run validate_spec.py <path>`.

Exit codes:
  0  valid
  1  invalid (validation or parse error)
  2  usage error / file not found
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _kebab(value: str, field: str = "id") -> str:
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


_AC_REQUIRED = {CapabilityState.PLANNED, CapabilityState.IN_PROGRESS, CapabilityState.BUILT}


class UserStory(BaseModel):
    model_config = {"populate_by_name": True}

    as_: str = Field(alias="as")
    want: str
    benefit: str


class Behavior(BaseModel):
    id: str
    description: str
    examples: list[str] = []
    acceptance_criteria: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _v_id(cls, v: str) -> str:
        return _kebab(v)


class NonGoal(BaseModel):
    id: str
    text: str
    rationale: str = ""
    aliases: list[str] = []

    @field_validator("id")
    @classmethod
    def _v_id(cls, v: str) -> str:
        return _kebab(v)

    @field_validator("aliases")
    @classmethod
    def _v_aliases(cls, v: list[str]) -> list[str]:
        return [_kebab(a, "alias") for a in v]


class GivenWhenThen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    given: str = Field(..., min_length=1)
    when: str = Field(..., min_length=1)
    then: str = Field(..., min_length=1)


class DoneEnoughBlock(BaseModel):
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
    acceptance_criteria: list[str] = []
    examples: list[GivenWhenThen] = []
    done_enough: list[DoneEnoughBlock] = []
    excluded: list[str] = []
    open_questions: list[str] = []
    tickets: list[str] = []
    aliases: list[str] = []
    created_at: datetime
    last_updated: datetime
    state_changed_at: datetime

    @field_validator("id")
    @classmethod
    def _v_id(cls, v: str) -> str:
        return _kebab(v)

    @field_validator("aliases")
    @classmethod
    def _v_aliases(cls, v: list[str]) -> list[str]:
        return [_kebab(a, "alias") for a in v]

    @model_validator(mode="after")
    def _v_ac_for_state(self) -> "Capability":
        if self.state not in _AC_REQUIRED:
            return self
        if self.acceptance_criteria or self.behaviors:
            return self
        raise ValueError(
            f"capability {self.id!r} (state={self.state.value}) requires at least one "
            "acceptance criterion, either capability-level or via behaviors"
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
    def _v_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"unsupported spec_version {v} (this validator is v1)")
        return v


def validate(path: Path) -> int:
    try:
        text = path.read_text()
    except FileNotFoundError:
        print(f"error: {path} not found", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"error: cannot read {path}: {e}", file=sys.stderr)
        return 2

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        print(f"YAML parse error: {e}", file=sys.stderr)
        return 1

    if not isinstance(data, dict):
        print(f"error: top-level YAML must be a mapping, got {type(data).__name__}", file=sys.stderr)
        return 1

    try:
        StructuredSpec.model_validate(data)
    except ValidationError as e:
        print(f"spec does not match schema:\n{e}", file=sys.stderr)
        return 1

    print(f"ok: {path} is a valid project spec")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a jig project.structured.yaml file.")
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path(".jig/spec/project.structured.yaml"),
        help="Path to the YAML file (default: .jig/spec/project.structured.yaml)",
    )
    args = parser.parse_args()
    return validate(args.path)


if __name__ == "__main__":
    sys.exit(main())
