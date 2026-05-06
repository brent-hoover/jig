---
title: Project Spec Schema — Implementation Plan
type: plan
status: draft
owner: brent
created: 2026-04-27
updated: 2026-04-27
design: ./design.md
---

# Project Spec Schema — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pydantic-validated `project.structured.yaml`, brief format with explicit IDs, regeneration semantics, URI resolver, capability-aware MCP tools, and a brief-approval workflow step.

**Architecture:** Schema lives in `jig/spec_schema.py`. Brief parser in new `jig/brief_parser.py`. URI resolver branch in `jig/context_resolver.py`. MCP tools and handlers extended in `jig/mcp_server.py` and `jig/init_mcp.py`. Workflow integration in `jig/init_workflow.py`. Humans only edit the brief; spec-gen owns the structured form.

**Tech Stack:** Python 3.12, Pydantic v2, pytest (asyncio mode), uv for dependency management, ruff for lint/format.

---

## Overview

Nine ordered phases. Each phase ends in a green test suite and a commit. Phases 1–5 are pure-data and pure-Python (schema, parser, merge, resolver, tools); 6 is role/prompt rewrites; 7 is workflow integration; 8 is docs split; 9 is the end-to-end smoke. Each phase can be executed by a fresh subagent given this plan plus the design doc.

## Preconditions

- [ ] Design doc approved (`./design.md`).
- [ ] Latest `develop` branch.
- [ ] `uv sync` runs cleanly.
- [ ] Test suite green (`uv run pytest tests/ -q`).

---

## Phase 1: Schema + handler validation

**What:** Define `StructuredSpec`, `Capability`, `Behavior`, `NonGoal`, `UserStory`, `CapabilityState`. Wire validation into `handle_spec_publish`. Add `Ticket.derived_from` field for downstream ticket→capability linkage.

**Why:** Foundation. Every later phase produces or consumes these models.

**Verify:** `uv run pytest tests/test_spec_schema.py -v` passes; existing `tests/test_init_mcp_spec.py` still passes after migration to schema-valid fixtures.

### 1.1 Create the schema module

**Files:**
- Create: `jig/spec_schema.py`
- Create: `tests/test_spec_schema.py`

- [ ] **Step 1: Write the failing test for `CapabilityState` enum**

```python
# tests/test_spec_schema.py
from jig.spec_schema import CapabilityState


def test_capability_state_values():
    assert {s.value for s in CapabilityState} == {
        "backlog", "planned", "in_progress", "built", "archived",
    }
```

- [ ] **Step 2: Run test to verify it fails**

`uv run pytest tests/test_spec_schema.py::test_capability_state_values -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jig.spec_schema'`.

- [ ] **Step 3: Create the module with the enum**

```python
# jig/spec_schema.py
"""Pydantic schema for the structured project spec
(``.jig/spec/project.structured.yaml``).

Owned by the spec-generator agent — humans do NOT edit the structured
form directly. The brief (``docs/brief.md``) is the source of
truth for content and IDs; this module validates the projection
spec-gen produces from the brief.

See ``docs/project-spec-schema/design.md`` for the full design.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class CapabilityState(str, Enum):
    BACKLOG = "backlog"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BUILT = "built"
    ARCHIVED = "archived"
```

- [ ] **Step 4: Run test to verify it passes**

`uv run pytest tests/test_spec_schema.py::test_capability_state_values -v`
Expected: PASS.

- [ ] **Step 5: Add tests for `UserStory`**

```python
# tests/test_spec_schema.py
from jig.spec_schema import UserStory


def test_user_story_uses_as_alias():
    """`as` is a Python keyword, so the field is `as_` with `alias='as'`.
    YAML/dict input uses `as`."""
    s = UserStory.model_validate({
        "as": "busy professional",
        "want": "due dates",
        "benefit": "I never miss a deadline",
    })
    assert s.as_ == "busy professional"
    assert s.want == "due dates"
    assert s.benefit == "I never miss a deadline"


def test_user_story_serializes_with_as_alias():
    s = UserStory(**{"as": "x", "want": "y", "benefit": "z"})
    assert s.model_dump(by_alias=True) == {"as": "x", "want": "y", "benefit": "z"}
```

- [ ] **Step 6: Add `UserStory` model**

```python
# jig/spec_schema.py (add after CapabilityState)


class UserStory(BaseModel):
    """Optional WHO + WHAT + WHY framing for a capability.

    Field name ``as_`` because ``as`` is a Python keyword; YAML/dict
    surface uses ``as`` via the alias.
    """
    as_: str = Field(alias="as")
    want: str
    benefit: str

    model_config = {"populate_by_name": True}
```

- [ ] **Step 7: Run user-story tests**

`uv run pytest tests/test_spec_schema.py -v -k user_story`
Expected: 2 PASS.

- [ ] **Step 8: Add tests for `Behavior`**

```python
# tests/test_spec_schema.py
import pytest
from pydantic import ValidationError

from jig.spec_schema import Behavior


def test_behavior_minimal_valid():
    b = Behavior(
        id="set-due-date",
        description="User attaches a date to any todo.",
        acceptance_criteria=["A date can be attached to any todo."],
    )
    assert b.id == "set-due-date"
    assert b.examples == []


def test_behavior_requires_at_least_one_acceptance_criterion():
    """AC mandatory per design — ≥1 string required at the schema level."""
    with pytest.raises(ValidationError) as exc:
        Behavior(
            id="set-due-date",
            description="x",
            acceptance_criteria=[],
        )
    assert "acceptance_criteria" in str(exc.value)


def test_behavior_id_must_be_kebab_slug():
    with pytest.raises(ValidationError):
        Behavior(id="Has Caps", description="x", acceptance_criteria=["y"])
    with pytest.raises(ValidationError):
        Behavior(id="has_underscores", description="x", acceptance_criteria=["y"])
    with pytest.raises(ValidationError):
        Behavior(id="-leading-hyphen", description="x", acceptance_criteria=["y"])
```

- [ ] **Step 9: Add `Behavior` model with kebab-slug validation**

```python
# jig/spec_schema.py (add after UserStory)
import re

# Kebab-case slug: starts with letter or digit, then letters/digits/hyphens.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _kebab_slug(value: str) -> str:
    if not _SLUG_RE.match(value):
        raise ValueError(
            f"id must be kebab-case (^[a-z0-9][a-z0-9-]*$), got {value!r}"
        )
    return value


class Behavior(BaseModel):
    id: str
    description: str
    examples: list[str] = []
    acceptance_criteria: list[str] = Field(min_length=1)

    @classmethod
    def __get_validators__(cls):
        yield from super().__get_validators__()

    # Pydantic v2: use field_validator
    from pydantic import field_validator

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return _kebab_slug(v)
```

> Note: Pydantic v2 doesn't support `__get_validators__` — use `field_validator` only. Fix the snippet above by removing the `__get_validators__` stub:

```python
# jig/spec_schema.py — corrected Behavior
from pydantic import BaseModel, Field, field_validator


class Behavior(BaseModel):
    id: str
    description: str
    examples: list[str] = []
    acceptance_criteria: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return _kebab_slug(v)
```

- [ ] **Step 10: Run behavior tests**

`uv run pytest tests/test_spec_schema.py -v -k behavior`
Expected: 3 PASS.

- [ ] **Step 11: Add tests for `NonGoal`**

```python
# tests/test_spec_schema.py
from jig.spec_schema import NonGoal


def test_non_goal_minimal():
    ng = NonGoal(id="no-multi-user", text="Multi-user / sharing")
    assert ng.rationale == ""
    assert ng.aliases == []


def test_non_goal_with_aliases_and_rationale():
    ng = NonGoal(
        id="no-multi-user",
        text="Multi-user / sharing",
        rationale="Single-user is the explicit point",
        aliases=["no-collab"],
    )
    assert ng.rationale.startswith("Single-user")
    assert ng.aliases == ["no-collab"]


def test_non_goal_id_must_be_kebab_slug():
    with pytest.raises(ValidationError):
        NonGoal(id="No Multi User", text="x")


def test_non_goal_aliases_must_be_kebab_slugs():
    with pytest.raises(ValidationError):
        NonGoal(id="no-x", text="x", aliases=["No Caps"])
```

- [ ] **Step 12: Add `NonGoal` model**

```python
# jig/spec_schema.py (add after Behavior)


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
        for a in v:
            _kebab_slug(a)
        return v
```

- [ ] **Step 13: Run non-goal tests**

`uv run pytest tests/test_spec_schema.py -v -k non_goal`
Expected: 4 PASS.

- [ ] **Step 14: Commit progress**

```bash
git add jig/spec_schema.py tests/test_spec_schema.py
git commit -m "feat(spec-schema): CapabilityState, UserStory, Behavior, NonGoal"
```

### 1.2 Capability model with state-aware AC validation

**Files:**
- Modify: `jig/spec_schema.py`
- Modify: `tests/test_spec_schema.py`

- [ ] **Step 1: Write tests for the `Capability` model**

```python
# tests/test_spec_schema.py
from datetime import datetime, timezone

from jig.spec_schema import Capability, CapabilityState


def _now() -> datetime:
    return datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


def _ts() -> dict:
    """Common timestamp kwargs."""
    n = _now()
    return {"created_at": n, "last_updated": n, "state_changed_at": n}


def test_capability_minimal_backlog():
    """Backlog capability with no behaviors and no AC is valid."""
    c = Capability(
        id="due-dates",
        title="Due dates",
        state=CapabilityState.BACKLOG,
        **_ts(),
    )
    assert c.behaviors == []
    assert c.acceptance_criteria == []


def test_capability_planned_requires_ac_somewhere():
    """state=planned with no behaviors AND no capability-level AC is invalid."""
    with pytest.raises(ValidationError) as exc:
        Capability(
            id="due-dates",
            title="Due dates",
            state=CapabilityState.PLANNED,
            **_ts(),
        )
    assert "acceptance" in str(exc.value).lower()


def test_capability_planned_with_capability_level_ac_is_valid():
    c = Capability(
        id="blue-icon",
        title="Change icon to blue",
        state=CapabilityState.PLANNED,
        acceptance_criteria=["Icon's primary color is the brand blue."],
        **_ts(),
    )
    assert c.acceptance_criteria == ["Icon's primary color is the brand blue."]


def test_capability_planned_with_behavior_having_ac_is_valid():
    c = Capability(
        id="due-dates",
        title="Due dates",
        state=CapabilityState.PLANNED,
        behaviors=[
            Behavior(
                id="set-due-date",
                description="x",
                acceptance_criteria=["a date can be set"],
            ),
        ],
        **_ts(),
    )
    assert len(c.behaviors) == 1


def test_capability_archived_does_not_require_ac():
    c = Capability(
        id="old-thing",
        title="Old thing",
        state=CapabilityState.ARCHIVED,
        **_ts(),
    )
    assert c.acceptance_criteria == []
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_spec_schema.py -v -k capability`
Expected: FAIL — `Capability` not yet defined.

- [ ] **Step 3: Add `Capability` model with cross-field validator**

```python
# jig/spec_schema.py (add after NonGoal)
from pydantic import model_validator

_AC_REQUIRED_STATES = {
    CapabilityState.PLANNED,
    CapabilityState.IN_PROGRESS,
    CapabilityState.BUILT,
}


class Capability(BaseModel):
    id: str
    title: str
    state: CapabilityState
    summary: str = ""
    user_story: UserStory | None = None
    behaviors: list[Behavior] = []
    acceptance_criteria: list[str] = []   # capability-level, used when no behaviors
    excluded: list[str] = []
    open_questions: list[str] = []
    tickets: list[str] = []                # rebuilt by spec-gen from ticket store
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
        for a in v:
            _kebab_slug(a)
        return v

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
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_spec_schema.py -v -k capability`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/spec_schema.py tests/test_spec_schema.py
git commit -m "feat(spec-schema): Capability with state-aware AC validation"
```

### 1.3 StructuredSpec top-level model

**Files:**
- Modify: `jig/spec_schema.py`
- Modify: `tests/test_spec_schema.py`

- [ ] **Step 1: Write tests for `StructuredSpec`**

```python
# tests/test_spec_schema.py
from jig.spec_schema import StructuredSpec


def test_structured_spec_minimal_empty():
    s = StructuredSpec(
        name="todoapp",
        summary="A simple todo list manager.",
        generated_at=_now(),
    )
    assert s.capabilities == []
    assert s.non_goals == []
    assert s.spec_version == 1


def test_structured_spec_round_trips_through_yaml():
    import yaml
    s = StructuredSpec(
        name="x",
        summary="y",
        capabilities=[
            Capability(
                id="c1",
                title="Cap 1",
                state=CapabilityState.BACKLOG,
                **_ts(),
            ),
        ],
        non_goals=[NonGoal(id="ng1", text="not this")],
        generated_at=_now(),
    )
    dumped = yaml.safe_dump(s.model_dump(mode="json", by_alias=True))
    parsed = yaml.safe_load(dumped)
    rebuilt = StructuredSpec.model_validate(parsed)
    assert rebuilt.name == "x"
    assert rebuilt.capabilities[0].id == "c1"
    assert rebuilt.non_goals[0].text == "not this"
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_spec_schema.py -v -k structured_spec`
Expected: FAIL.

- [ ] **Step 3: Add `StructuredSpec` model**

```python
# jig/spec_schema.py (add at the end of file)


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
        for ng in self.non_goals:
            if ng.id == key or key in ng.aliases:
                return ng
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_spec_schema.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/spec_schema.py tests/test_spec_schema.py
git commit -m "feat(spec-schema): StructuredSpec with id+alias lookup helpers"
```

### 1.4 Wire schema validation into `handle_spec_publish`

**Files:**
- Modify: `jig/init_mcp.py:176-216` (the `handle_spec_publish` function)
- Modify: `tests/test_init_mcp_spec.py` — every existing test calls `handle_spec_publish` with placeholder YAML; needs schema-valid replacements

- [ ] **Step 1: Read the current handler**

Run: `sed -n '176,216p' jig/init_mcp.py` (or open the file)
Note its current shape — accepts arbitrary `yaml_content`, only checks `yaml.safe_load`. We'll add `StructuredSpec.model_validate` after the YAML parse and before writing the file.

- [ ] **Step 2: Write a failing test for the new validation**

```python
# tests/test_init_mcp_spec.py — add at end of file
@pytest.mark.asyncio
async def test_spec_publish_rejects_schema_invalid_yaml(wired):
    """A YAML that parses but doesn't match StructuredSpec should raise
    before writing the file."""
    with pytest.raises(ValueError, match="schema"):
        await handle_spec_publish(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            yaml_content="capabilities: not-a-list-but-a-string\n",
            advisory_notes=[],
            author="spec-generator",
        )
    spec_file = wired["project_path"] / ".jig" / "spec" / "project.structured.yaml"
    assert not spec_file.exists()
```

- [ ] **Step 3: Run test to verify it fails**

`uv run pytest tests/test_init_mcp_spec.py::test_spec_publish_rejects_schema_invalid_yaml -v`
Expected: FAIL — current handler accepts any parseable YAML.

- [ ] **Step 4: Add Pydantic validation to `handle_spec_publish`**

```python
# jig/init_mcp.py — modify handle_spec_publish
# Add this import at the top of the file:
from jig.spec_schema import StructuredSpec

# In handle_spec_publish, replace:
#     try:
#         yaml.safe_load(yaml_content)
#     except yaml.YAMLError as e:
#         raise ValueError(f"cannot parse spec YAML: {e}") from e
#     atomic_write_text(_spec_path(project_path), yaml_content)
# With:

    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise ValueError(f"cannot parse spec YAML: {e}") from e
    try:
        StructuredSpec.model_validate(data)
    except Exception as e:
        # Pydantic ValidationError or any other model-level rejection
        raise ValueError(f"spec does not match schema: {e}") from e
    atomic_write_text(_spec_path(project_path), yaml_content)
```

- [ ] **Step 5: Update existing fixtures in `tests/test_init_mcp_spec.py` to schema-valid YAML**

Every `handle_spec_publish` call that uses `"name: x\n"` or `"name: myproj\ncapabilities: {}\n"` needs to include `summary` and `generated_at`. Helper at the top of the file:

```python
# tests/test_init_mcp_spec.py — add near top, after imports
from datetime import datetime, timezone


def _valid_spec_yaml(name: str = "myproj", capabilities: str = "[]") -> str:
    """Minimal schema-valid spec YAML for tests."""
    return (
        f"name: {name}\n"
        "summary: a project\n"
        f"capabilities: {capabilities}\n"
        "non_goals: []\n"
        f"generated_at: '{datetime.now(timezone.utc).isoformat()}'\n"
        "spec_version: 1\n"
    )
```

Then update each test that previously used `"name: myproj\ncapabilities: {}\n"` or `"name: x\n"` to use `_valid_spec_yaml(...)` (or inline). Change `capabilities: {}` (dict) to `capabilities: []` (list — schema is a list, not a dict).

For the `test_spec_publish_writes_file_and_emits_event` test specifically:

```python
# Before:
yaml_str = "name: myproj\ncapabilities: {}\n"
# After:
yaml_str = _valid_spec_yaml(name="myproj")
```

Apply the same pattern to all callers in this file.

- [ ] **Step 6: Run the spec tests to verify they all pass**

`uv run pytest tests/test_init_mcp_spec.py -v`
Expected: all PASS (previously failing tests now pass with schema-valid fixtures).

- [ ] **Step 7: Run the full suite to catch ripples**

`uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add jig/init_mcp.py tests/test_init_mcp_spec.py
git commit -m "feat(spec): validate spec_publish against StructuredSpec schema"
```

### 1.5 Add `Ticket.derived_from` field

**Files:**
- Modify: `jig/ticket.py:76-95` (the `Ticket` model)
- Modify: `tests/test_ticket.py` (or create if absent)

- [ ] **Step 1: Locate the Ticket model**

Run: `grep -n "class Ticket" jig/ticket.py`
Note the line number; `derived_from` slots in alongside the other optional metadata fields.

- [ ] **Step 2: Write a failing test**

```python
# tests/test_ticket.py — append (or create file with imports if absent)
from jig.ticket import Ticket, WorkType


def test_ticket_derived_from_defaults_none():
    t = Ticket(work_type=WorkType.FEATURE, title="t", created_by="cli")
    assert t.derived_from is None


def test_ticket_derived_from_accepts_uri():
    t = Ticket(
        work_type=WorkType.FEATURE,
        title="t",
        created_by="cli",
        derived_from="project://spec/capabilities/due-dates",
    )
    assert t.derived_from == "project://spec/capabilities/due-dates"
```

- [ ] **Step 3: Run test to verify it fails**

`uv run pytest tests/test_ticket.py -v -k derived_from`
Expected: FAIL — `derived_from` not on the model.

- [ ] **Step 4: Add the field to `Ticket`**

```python
# jig/ticket.py — inside class Ticket(StoreModel), alongside the other
# optional metadata fields (after `assignee`, near `parent_id`):
    derived_from: str | None = None  # e.g. "project://spec/capabilities/due-dates"
```

- [ ] **Step 5: Run tests to verify they pass**

`uv run pytest tests/test_ticket.py -v -k derived_from`
Expected: PASS.

- [ ] **Step 6: Run full suite to catch ripples**

`uv run pytest tests/ -q`
Expected: all PASS (the field is optional with a default, no existing call site is affected).

- [ ] **Step 7: Commit**

```bash
git add jig/ticket.py tests/test_ticket.py
git commit -m "feat(ticket): add derived_from URI field for spec linkage"
```

---

## Phase 2: Brief parser

**What:** Hand-rolled parser that takes brief markdown text and produces intermediate `BriefCapability` / `BriefNonGoal` dataclasses, plus structured error reports for format violations.

**Why:** Spec-gen needs a deterministic way to extract capabilities, behaviors, AC, and IDs from the brief. Hand-rolled per design decision — no external markdown library.

**Verify:** `uv run pytest tests/test_brief_parser.py -v` passes; covers all six format rules from the design doc.

### 2.1 Define intermediate dataclasses and parser entry point

**Files:**
- Create: `jig/brief_parser.py`
- Create: `tests/test_brief_parser.py`

- [ ] **Step 1: Write the test for `BriefCapability` shape**

```python
# tests/test_brief_parser.py
from jig.brief_parser import BriefCapability, BriefBehavior, BriefNonGoal


def test_brief_capability_dataclass_shape():
    c = BriefCapability(
        id="due-dates",
        title="Due dates",
        section="planned_committed",
        summary="users can set due dates",
        user_story=None,
        behaviors=[],
        capability_acceptance_criteria=[],
        excluded=[],
        open_questions=[],
        aliases=[],
    )
    assert c.id == "due-dates"
    assert c.section == "planned_committed"


def test_brief_behavior_dataclass_shape():
    b = BriefBehavior(
        id="set-due-date",
        description="set a date",
        examples=[],
        acceptance_criteria=["the date is saved"],
    )
    assert b.id == "set-due-date"


def test_brief_non_goal_dataclass_shape():
    ng = BriefNonGoal(
        id="no-multi-user",
        text="Multi-user",
        rationale="single-user is the point",
        aliases=[],
    )
    assert ng.id == "no-multi-user"
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_brief_parser.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Create the module with dataclasses**

```python
# jig/brief_parser.py
"""Parser for the brief markdown (``docs/brief.md``).

Hand-rolled so we control the anchor syntax (``{#id}`` for definitions,
``[id]`` for references) precisely. Output is an intermediate
representation consumed by the spec-generator's regen merge logic; this
module never produces ``StructuredSpec`` directly.

Format rules (each blocking gap if violated):

1. Every ``### <title>`` heading has a trailing ``{#slug}``.
2. Every bullet under Planned-not-committed / Backlog / Non-goals /
   Archived has a leading ``{#slug}``.
3. Every behavior bullet has a leading ``{#behavior-id}``. Every AC
   bullet has a leading ``[behavior-id]`` reference (or no behavior
   reference when the AC is capability-level).
4. AC ``[behavior-id]`` references resolve to a behavior in the same
   capability.
5. IDs are unique across the brief (capabilities, non-goals, behaviors
   namespaced per capability).
6. Aliases are unique across the brief, no collision with any ``id``.

See ``docs/project-spec-schema/design.md`` §"Brief format".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


BriefSection = Literal[
    "built",
    "planned_committed",
    "planned_not_committed",
    "backlog",
    "archived",
]


@dataclass
class BriefBehavior:
    id: str
    description: str
    examples: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)


@dataclass
class BriefUserStory:
    as_: str
    want: str
    benefit: str


@dataclass
class BriefCapability:
    id: str
    title: str
    section: BriefSection
    summary: str = ""
    user_story: BriefUserStory | None = None
    behaviors: list[BriefBehavior] = field(default_factory=list)
    capability_acceptance_criteria: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


@dataclass
class BriefNonGoal:
    id: str
    text: str
    rationale: str = ""
    aliases: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_brief_parser.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/brief_parser.py tests/test_brief_parser.py
git commit -m "feat(brief-parser): intermediate dataclasses (BriefCapability et al.)"
```

### 2.2 Anchor parsing (`{#id}` and `{#id aliases:a,b}`)

**Files:**
- Modify: `jig/brief_parser.py`
- Modify: `tests/test_brief_parser.py`

- [ ] **Step 1: Write tests for the anchor parser**

```python
# tests/test_brief_parser.py
from jig.brief_parser import parse_anchor, AnchorParseError


def test_parse_anchor_simple_id():
    a = parse_anchor("{#due-dates}")
    assert a.id == "due-dates"
    assert a.aliases == []


def test_parse_anchor_with_aliases():
    a = parse_anchor("{#deadlines aliases:due-dates,old-name}")
    assert a.id == "deadlines"
    assert a.aliases == ["due-dates", "old-name"]


def test_parse_anchor_rejects_non_kebab_id():
    with pytest.raises(AnchorParseError):
        parse_anchor("{#Has Caps}")


def test_parse_anchor_rejects_malformed():
    with pytest.raises(AnchorParseError):
        parse_anchor("{#}")           # empty id
    with pytest.raises(AnchorParseError):
        parse_anchor("{# leading-space}")
    with pytest.raises(AnchorParseError):
        parse_anchor("not an anchor")  # no braces


def test_parse_anchor_rejects_unknown_attr():
    with pytest.raises(AnchorParseError, match="unknown"):
        parse_anchor("{#x weird:y}")


# Add this import to the test file:
import pytest
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/test_brief_parser.py -v -k anchor`
Expected: FAIL.

- [ ] **Step 3: Implement the anchor parser**

```python
# jig/brief_parser.py — add at module level
import re


_ANCHOR_RE = re.compile(r"^\{#([a-z0-9][a-z0-9-]*)(?:\s+(.*?))?\}$")
_KEBAB_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class AnchorParseError(ValueError):
    """Raised when a {#id ...} anchor is malformed."""


@dataclass
class ParsedAnchor:
    id: str
    aliases: list[str]


def parse_anchor(text: str) -> ParsedAnchor:
    """Parse ``{#id}`` or ``{#id aliases:a,b}`` into a ParsedAnchor.

    Raises AnchorParseError on any malformation. Recognized attributes:
    ``aliases:a,b,c``. Unknown attribute keys are rejected.
    """
    text = text.strip()
    m = _ANCHOR_RE.match(text)
    if not m:
        raise AnchorParseError(f"malformed anchor: {text!r}")
    cap_id = m.group(1)
    attrs_raw = (m.group(2) or "").strip()
    aliases: list[str] = []
    if attrs_raw:
        for chunk in attrs_raw.split():
            if ":" not in chunk:
                raise AnchorParseError(
                    f"malformed attribute {chunk!r} in anchor {text!r}"
                )
            key, _, value = chunk.partition(":")
            if key != "aliases":
                raise AnchorParseError(
                    f"unknown anchor attribute {key!r} in {text!r}"
                )
            for alias in value.split(","):
                alias = alias.strip()
                if not _KEBAB_RE.match(alias):
                    raise AnchorParseError(
                        f"alias {alias!r} is not kebab-case in anchor {text!r}"
                    )
                aliases.append(alias)
    return ParsedAnchor(id=cap_id, aliases=aliases)
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_brief_parser.py -v -k anchor`
Expected: 5 PASS.

- [ ] **Step 5: Add a reference parser for `[id]`**

```python
# tests/test_brief_parser.py
from jig.brief_parser import parse_reference, ReferenceParseError


def test_parse_reference_simple():
    assert parse_reference("[set-due-date]") == "set-due-date"


def test_parse_reference_rejects_non_kebab():
    with pytest.raises(ReferenceParseError):
        parse_reference("[Set Due Date]")


def test_parse_reference_rejects_malformed():
    with pytest.raises(ReferenceParseError):
        parse_reference("set-due-date")    # no brackets
    with pytest.raises(ReferenceParseError):
        parse_reference("[]")              # empty
```

- [ ] **Step 6: Implement the reference parser**

```python
# jig/brief_parser.py — add after parse_anchor
_REFERENCE_RE = re.compile(r"^\[([a-z0-9][a-z0-9-]*)\]$")


class ReferenceParseError(ValueError):
    """Raised when an [id] reference is malformed."""


def parse_reference(text: str) -> str:
    """Parse ``[id]`` into the bare id string."""
    m = _REFERENCE_RE.match(text.strip())
    if not m:
        raise ReferenceParseError(f"malformed reference: {text!r}")
    return m.group(1)
```

- [ ] **Step 7: Run all anchor + reference tests**

`uv run pytest tests/test_brief_parser.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add jig/brief_parser.py tests/test_brief_parser.py
git commit -m "feat(brief-parser): {#id} anchor and [id] reference parsers"
```

### 2.3 Section parser

**Files:**
- Modify: `jig/brief_parser.py`
- Modify: `tests/test_brief_parser.py`

- [ ] **Step 1: Write a test for the section walker**

```python
# tests/test_brief_parser.py
from jig.brief_parser import split_into_sections


_SAMPLE_BRIEF = """\
# todoapp

A simple todo list manager.

## Built

(empty)

## Planned (committed)

### Due dates {#due-dates}

Users can give todos due dates.

**Behaviors:**
- {#set-due-date} Set a date

**Acceptance criteria:**
- [set-due-date] Date persists

## Non-goals

- {#no-multi-user} Multi-user
"""


def test_split_into_sections_returns_intro_and_section_bodies():
    parsed = split_into_sections(_SAMPLE_BRIEF)
    assert parsed.name == "todoapp"
    assert parsed.summary.strip() == "A simple todo list manager."
    assert "Built" in parsed.sections
    assert "Planned (committed)" in parsed.sections
    assert "Non-goals" in parsed.sections
    # Section content is the raw body after the H2 heading
    assert "(empty)" in parsed.sections["Built"]
    assert "Due dates" in parsed.sections["Planned (committed)"]


def test_split_into_sections_rejects_missing_h1():
    with pytest.raises(ValueError, match="H1"):
        split_into_sections("## Built\n")
```

- [ ] **Step 2: Run tests — expect FAIL**

`uv run pytest tests/test_brief_parser.py -v -k split_into_sections`
Expected: FAIL.

- [ ] **Step 3: Implement section split**

```python
# jig/brief_parser.py — add
@dataclass
class ParsedBrief:
    name: str
    summary: str
    sections: dict[str, str]   # section heading text → raw body


def split_into_sections(text: str) -> ParsedBrief:
    """Split brief markdown into intro + sections by H2 heading.

    Returns:
      ParsedBrief(name, summary, sections={heading: body, ...})

    Raises ValueError on missing H1 or duplicate H2.
    """
    lines = text.splitlines()
    name: str | None = None
    intro_lines: list[str] = []
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_body: list[str] = []

    def _flush_section():
        nonlocal current_body
        if current_section is not None:
            if current_section in sections:
                raise ValueError(
                    f"duplicate H2 section heading: {current_section!r}"
                )
            sections[current_section] = "\n".join(current_body).strip("\n")
            current_body = []

    for line in lines:
        if line.startswith("# ") and name is None:
            name = line[2:].strip()
            continue
        if line.startswith("## "):
            _flush_section()
            current_section = line[3:].strip()
            continue
        if current_section is None:
            intro_lines.append(line)
        else:
            current_body.append(line)
    _flush_section()

    if name is None:
        raise ValueError("brief is missing an H1 (project name)")

    summary = "\n".join(intro_lines).strip()
    return ParsedBrief(name=name, summary=summary, sections=sections)
```

- [ ] **Step 4: Run tests to verify**

`uv run pytest tests/test_brief_parser.py -v -k split_into_sections`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/brief_parser.py tests/test_brief_parser.py
git commit -m "feat(brief-parser): split brief into intro + named sections"
```

### 2.4 Capability parser (Built / Planned committed / Archived sections)

**Files:**
- Modify: `jig/brief_parser.py`
- Modify: `tests/test_brief_parser.py`

These sections contain `### <title> {#id}` headings followed by capability content blocks. Backlog and Planned-not-committed are bullet sections handled separately in §2.5.

- [ ] **Step 1: Write tests for elaborated capability parsing**

```python
# tests/test_brief_parser.py
from jig.brief_parser import parse_elaborated_section, BriefParseError


_PLANNED_BODY = """\
### Due dates {#due-dates}

Users can give todos due dates.

**User story:**
As a busy person, I want due dates so I never miss deadlines.

**Behaviors:**
- {#set-due-date} Set a date on any todo
- {#overdue-indicator} Show past-due todos in red

**Acceptance criteria:**
- [set-due-date] A date can be set
- [overdue-indicator] Past-due todos display in red

**Excluded:**
- Recurring dates
- Reminders

**Open questions:**
- Time component, or date only?

### Priorities {#priorities}

Three levels: high, medium, low.

**Acceptance criteria:**
- Default priority is medium
"""


def test_parse_elaborated_section_extracts_capabilities():
    caps = parse_elaborated_section(_PLANNED_BODY, section="planned_committed")
    assert len(caps) == 2

    dd = caps[0]
    assert dd.id == "due-dates"
    assert dd.title == "Due dates"
    assert dd.section == "planned_committed"
    assert "give todos due dates" in dd.summary
    assert dd.user_story is not None
    assert dd.user_story.as_ == "busy person"
    assert [b.id for b in dd.behaviors] == ["set-due-date", "overdue-indicator"]
    assert dd.behaviors[0].acceptance_criteria == ["A date can be set"]
    assert dd.behaviors[1].acceptance_criteria == ["Past-due todos display in red"]
    assert dd.excluded == ["Recurring dates", "Reminders"]
    assert dd.open_questions == ["Time component, or date only?"]
    assert dd.capability_acceptance_criteria == []

    pri = caps[1]
    assert pri.id == "priorities"
    assert pri.behaviors == []
    assert pri.capability_acceptance_criteria == ["Default priority is medium"]


def test_parse_elaborated_section_handles_aliases_in_anchor():
    body = "### Deadlines {#deadlines aliases:due-dates}\n\nProse.\n"
    caps = parse_elaborated_section(body, section="planned_committed")
    assert caps[0].id == "deadlines"
    assert caps[0].aliases == ["due-dates"]


def test_parse_elaborated_section_rejects_missing_anchor():
    body = "### Due dates\n\nProse.\n"
    with pytest.raises(BriefParseError, match="anchor"):
        parse_elaborated_section(body, section="planned_committed")


def test_parse_elaborated_section_rejects_ac_referencing_missing_behavior():
    body = """\
### X {#x}

**Behaviors:**
- {#b1} desc

**Acceptance criteria:**
- [b1] ok
- [b-missing] dangling
"""
    with pytest.raises(BriefParseError, match="b-missing"):
        parse_elaborated_section(body, section="planned_committed")


def test_parse_elaborated_section_rejects_when_planned_has_no_ac():
    body = """\
### X {#x}

Some prose, no behaviors, no AC block.
"""
    with pytest.raises(BriefParseError, match="acceptance"):
        parse_elaborated_section(body, section="planned_committed")
```

- [ ] **Step 2: Run tests — expect FAIL**

`uv run pytest tests/test_brief_parser.py -v -k parse_elaborated_section`
Expected: FAIL.

- [ ] **Step 3: Implement the elaborated section parser**

This is the largest single piece — split the section body on `### ` headings, parse each capability block. Each block extracts title + anchor, summary prose (until first `**...**` block), and labelled `**Behaviors:**` / `**User story:**` / `**Acceptance criteria:**` / `**Excluded:**` / `**Open questions:**` blocks.

```python
# jig/brief_parser.py — add
class BriefParseError(ValueError):
    """Raised when the brief markdown violates a format rule."""


_HEADING_ANCHOR_RE = re.compile(r"^### (.+?)\s+(\{#[^}]+\})\s*$")


def parse_elaborated_section(body: str, *, section: BriefSection) -> list[BriefCapability]:
    """Parse the body of a Built / Planned (committed) / Archived section.

    Each capability is introduced by ``### Title {#id}`` and may contain
    a summary paragraph plus labelled blocks: ``**User story:**``,
    ``**Behaviors:**``, ``**Acceptance criteria:**``, ``**Excluded:**``,
    ``**Open questions:**``.
    """
    blocks = _split_on_h3(body)
    out: list[BriefCapability] = []
    for raw_block in blocks:
        out.append(_parse_capability_block(raw_block, section=section))
    return out


def _split_on_h3(body: str) -> list[str]:
    """Split a section body into sub-blocks at each `### ` heading.

    Returns each block including its `### ` line. Empty result if no
    headings present.
    """
    lines = body.splitlines()
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("### "):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return ["\n".join(b) for b in blocks]


def _parse_capability_block(text: str, *, section: BriefSection) -> BriefCapability:
    lines = text.splitlines()
    if not lines or not lines[0].startswith("### "):
        raise BriefParseError(f"capability block missing ### heading: {text[:80]!r}")
    m = _HEADING_ANCHOR_RE.match(lines[0])
    if not m:
        raise BriefParseError(
            f"capability heading missing trailing {{#anchor}}: {lines[0]!r}"
        )
    title = m.group(1).strip()
    try:
        anchor = parse_anchor(m.group(2))
    except AnchorParseError as e:
        raise BriefParseError(str(e)) from e

    body_lines = lines[1:]
    blocks = _split_on_labels(body_lines)

    summary = blocks.pop("__intro__", "").strip()
    user_story = _parse_user_story_block(blocks.pop("User story", None))
    behaviors = _parse_behavior_block(blocks.pop("Behaviors", None))
    raw_ac_lines = _parse_bullet_lines(blocks.pop("Acceptance criteria", None))
    excluded = _parse_bullet_lines(blocks.pop("Excluded", None))
    open_questions = _parse_bullet_lines(blocks.pop("Open questions", None))
    if blocks:
        raise BriefParseError(
            f"unknown labelled block(s) in capability {anchor.id!r}: "
            f"{list(blocks.keys())}"
        )

    behavior_ids = {b.id for b in behaviors}
    capability_ac: list[str] = []
    for ac_line in raw_ac_lines:
        # AC bullet may start with [behavior-id] reference or be capability-level.
        ref, text = _split_ac_reference(ac_line)
        if ref is None:
            capability_ac.append(text)
        else:
            if ref not in behavior_ids:
                raise BriefParseError(
                    f"AC references missing behavior [{ref}] in capability "
                    f"{anchor.id!r}"
                )
            for b in behaviors:
                if b.id == ref:
                    b.acceptance_criteria.append(text)
                    break

    # Format rule 5: planned/built/archived capabilities under an
    # elaborated section need AC somewhere when behaviors exist.
    # Rule already enforced by Pydantic min_length=1 for behaviors.
    # Capability-with-no-behaviors needs capability-level AC for
    # planned/in_progress/built (archived skipped per design).
    if section in ("planned_committed", "built") and not behaviors:
        if not capability_ac:
            raise BriefParseError(
                f"capability {anchor.id!r} has no behaviors and no "
                "capability-level acceptance criteria"
            )

    return BriefCapability(
        id=anchor.id,
        title=title,
        section=section,
        summary=summary,
        user_story=user_story,
        behaviors=behaviors,
        capability_acceptance_criteria=capability_ac,
        excluded=excluded,
        open_questions=open_questions,
        aliases=anchor.aliases,
    )


_LABEL_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")


def _split_on_labels(lines: list[str]) -> dict[str, str]:
    """Split body lines into labelled blocks. Lines before the first
    labelled block become the ``__intro__`` block (the summary prose).
    """
    blocks: dict[str, list[str]] = {"__intro__": []}
    current = "__intro__"
    for line in lines:
        m = _LABEL_RE.match(line.strip())
        if m:
            current = m.group(1).strip()
            blocks.setdefault(current, [])
            continue
        blocks[current].append(line)
    return {k: "\n".join(v).strip("\n") for k, v in blocks.items()}


def _parse_user_story_block(text: str | None) -> BriefUserStory | None:
    if text is None or not text.strip():
        return None
    # Single line: "As a X, I want Y so that Z." (or with "so" instead of "so that")
    body = " ".join(line.strip() for line in text.splitlines() if line.strip())
    pattern = re.compile(
        r"^As an? (.+?), I want (.+?) so(?: that)? (.+?)\.?$",
        re.IGNORECASE,
    )
    m = pattern.match(body)
    if not m:
        raise BriefParseError(
            "user story must read 'As a X, I want Y so [that] Z.': "
            f"got {body!r}"
        )
    return BriefUserStory(as_=m.group(1).strip(), want=m.group(2).strip(),
                          benefit=m.group(3).strip())


def _parse_behavior_block(text: str | None) -> list[BriefBehavior]:
    if text is None:
        return []
    out: list[BriefBehavior] = []
    for line in _bullet_lines(text):
        # Each behavior bullet: `{#id} description`
        anchor_text, _, rest = line.partition(" ")
        try:
            anchor = parse_anchor(anchor_text)
        except AnchorParseError as e:
            raise BriefParseError(
                f"behavior bullet missing leading {{#id}}: {line!r}"
            ) from e
        if not rest.strip():
            raise BriefParseError(
                f"behavior bullet has anchor but no description: {line!r}"
            )
        out.append(BriefBehavior(id=anchor.id, description=rest.strip()))
    return out


def _parse_bullet_lines(text: str | None) -> list[str]:
    if text is None:
        return []
    return list(_bullet_lines(text))


def _bullet_lines(text: str):
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            raise BriefParseError(f"expected bullet line, got: {line!r}")
        yield stripped[2:].strip()


def _split_ac_reference(line: str) -> tuple[str | None, str]:
    """Split an AC bullet body into (behavior_ref, ac_text).

    AC may start with ``[behavior-id]`` (behavior-level) or have no
    bracketed prefix (capability-level).
    """
    line = line.strip()
    if not line.startswith("["):
        return None, line
    end = line.find("]")
    if end == -1:
        raise BriefParseError(f"unterminated [reference] in AC: {line!r}")
    ref_text = line[: end + 1]
    rest = line[end + 1 :].strip()
    try:
        ref_id = parse_reference(ref_text)
    except ReferenceParseError as e:
        raise BriefParseError(str(e)) from e
    return ref_id, rest
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/test_brief_parser.py -v -k parse_elaborated_section`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/brief_parser.py tests/test_brief_parser.py
git commit -m "feat(brief-parser): elaborated capability sections (Built / Planned / Archived)"
```

### 2.5 Bullet-section parser (Backlog, Planned-not-committed)

**Files:**
- Modify: `jig/brief_parser.py`
- Modify: `tests/test_brief_parser.py`

These sections are simple `- {#id} text` bullets — no behaviors, no AC.

- [ ] **Step 1: Write tests**

```python
# tests/test_brief_parser.py
from jig.brief_parser import parse_bullet_section


def test_parse_bullet_section_extracts_capabilities():
    body = """\
- {#mobile-app} Mobile app
- {#shortcuts} Keyboard shortcuts
"""
    caps = parse_bullet_section(body, section="backlog")
    assert [c.id for c in caps] == ["mobile-app", "shortcuts"]
    assert caps[0].title == "Mobile app"
    assert caps[0].section == "backlog"
    assert caps[0].behaviors == []
    assert caps[0].capability_acceptance_criteria == []


def test_parse_bullet_section_rejects_missing_anchor():
    body = "- Mobile app\n"
    with pytest.raises(BriefParseError, match="anchor"):
        parse_bullet_section(body, section="backlog")


def test_parse_bullet_section_rejects_behavior_blocks():
    body = """\
- {#x} Some idea
  **Behaviors:**
  - {#b} thing
"""
    with pytest.raises(BriefParseError, match="bullet section"):
        parse_bullet_section(body, section="backlog")


def test_parse_bullet_section_with_aliases():
    body = "- {#deadlines aliases:due-dates} Deadline tracking\n"
    caps = parse_bullet_section(body, section="planned_not_committed")
    assert caps[0].aliases == ["due-dates"]
```

- [ ] **Step 2: Implement**

```python
# jig/brief_parser.py — add
def parse_bullet_section(body: str, *, section: BriefSection) -> list[BriefCapability]:
    """Parse Backlog or Planned-not-committed body — flat bullet list."""
    out: list[BriefCapability] = []
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("**"):
            raise BriefParseError(
                f"bullet section {section!r} cannot contain labelled blocks: "
                f"{stripped!r}"
            )
        if not stripped.startswith("- "):
            raise BriefParseError(
                f"bullet section {section!r} expects '- {{#id}} text' lines, "
                f"got: {raw_line!r}"
            )
        body_text = stripped[2:].strip()
        anchor_text, _, title = body_text.partition(" ")
        try:
            anchor = parse_anchor(anchor_text)
        except AnchorParseError as e:
            raise BriefParseError(
                f"bullet missing leading anchor: {raw_line!r}"
            ) from e
        if not title.strip():
            raise BriefParseError(
                f"bullet has anchor but no title: {raw_line!r}"
            )
        out.append(BriefCapability(
            id=anchor.id,
            title=title.strip(),
            section=section,
            aliases=anchor.aliases,
        ))
    return out
```

- [ ] **Step 3: Run tests**

`uv run pytest tests/test_brief_parser.py -v -k parse_bullet_section`
Expected: 4 PASS.

- [ ] **Step 4: Commit**

```bash
git add jig/brief_parser.py tests/test_brief_parser.py
git commit -m "feat(brief-parser): bullet sections (Backlog / Planned-not-committed)"
```

### 2.6 Non-goals parser

**Files:**
- Modify: `jig/brief_parser.py`
- Modify: `tests/test_brief_parser.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_brief_parser.py
from jig.brief_parser import parse_non_goals_section


def test_parse_non_goals_extracts_text_and_rationale():
    body = """\
- {#no-multi-user} Multi-user / sharing — single-user is the explicit point
- {#no-mobile} Native mobile app
"""
    ng = parse_non_goals_section(body)
    assert [n.id for n in ng] == ["no-multi-user", "no-mobile"]
    assert ng[0].text == "Multi-user / sharing"
    assert ng[0].rationale == "single-user is the explicit point"
    assert ng[1].rationale == ""


def test_parse_non_goals_with_aliases():
    body = "- {#no-multi-user aliases:no-collab} Multi-user\n"
    ng = parse_non_goals_section(body)
    assert ng[0].aliases == ["no-collab"]


def test_parse_non_goals_rejects_missing_anchor():
    body = "- Multi-user\n"
    with pytest.raises(BriefParseError, match="anchor"):
        parse_non_goals_section(body)
```

- [ ] **Step 2: Implement**

```python
# jig/brief_parser.py — add
def parse_non_goals_section(body: str) -> list[BriefNonGoal]:
    """Parse the ``## Non-goals`` body. Each line is
    ``- {#id} text`` or ``- {#id} text — rationale``.
    """
    out: list[BriefNonGoal] = []
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            raise BriefParseError(
                f"non-goals section expects '- {{#id}} text' lines, got: "
                f"{raw_line!r}"
            )
        body_text = stripped[2:].strip()
        anchor_text, _, rest = body_text.partition(" ")
        try:
            anchor = parse_anchor(anchor_text)
        except AnchorParseError as e:
            raise BriefParseError(str(e)) from e
        if not rest.strip():
            raise BriefParseError(
                f"non-goal bullet missing text: {raw_line!r}"
            )
        # Split text and rationale on em-dash or " — " (en-dash too).
        for sep in (" — ", " – ", " -- "):
            if sep in rest:
                text, rationale = rest.split(sep, 1)
                break
        else:
            text, rationale = rest, ""
        out.append(BriefNonGoal(
            id=anchor.id,
            text=text.strip(),
            rationale=rationale.strip(),
            aliases=anchor.aliases,
        ))
    return out
```

- [ ] **Step 3: Run tests**

`uv run pytest tests/test_brief_parser.py -v -k parse_non_goals`
Expected: 3 PASS.

- [ ] **Step 4: Commit**

```bash
git add jig/brief_parser.py tests/test_brief_parser.py
git commit -m "feat(brief-parser): non-goals section"
```

### 2.7 Top-level `parse_brief` integrator

**Files:**
- Modify: `jig/brief_parser.py`
- Modify: `tests/test_brief_parser.py`

- [ ] **Step 1: Write a test for end-to-end brief parsing**

```python
# tests/test_brief_parser.py
from jig.brief_parser import parse_brief, ParsedBrief


_FULL_BRIEF = """\
# todoapp

A simple web-based todo list manager.

## Built

(empty)

## Planned (committed)

### Due dates {#due-dates}

Users can give todos due dates.

**Behaviors:**
- {#set-due-date} Set a date

**Acceptance criteria:**
- [set-due-date] Date persists

## Planned (not yet committed)

- {#labels} Labels

## Backlog

- {#mobile-app} Mobile app

## Non-goals

- {#no-multi-user} Multi-user — single-user only
"""


def test_parse_brief_end_to_end():
    result = parse_brief(_FULL_BRIEF)
    assert result.name == "todoapp"
    assert "todo list manager" in result.summary
    cap_ids = [c.id for c in result.capabilities]
    assert cap_ids == ["due-dates", "labels", "mobile-app"]
    assert [c.section for c in result.capabilities] == [
        "planned_committed",
        "planned_not_committed",
        "backlog",
    ]
    assert [n.id for n in result.non_goals] == ["no-multi-user"]


def test_parse_brief_rejects_duplicate_capability_id():
    body = """\
# x
intro

## Backlog

- {#dup} A
- {#dup} B
"""
    with pytest.raises(BriefParseError, match="duplicate"):
        parse_brief(body)


def test_parse_brief_rejects_alias_collision():
    body = """\
# x
intro

## Backlog

- {#a} thing one
- {#b aliases:a} thing two
"""
    with pytest.raises(BriefParseError, match="alias"):
        parse_brief(body)
```

- [ ] **Step 2: Implement `parse_brief`**

```python
# jig/brief_parser.py — add
@dataclass
class ParsedBriefResult:
    name: str
    summary: str
    capabilities: list[BriefCapability]
    non_goals: list[BriefNonGoal]


# Map of recognized H2 section names → parser kind.
_SECTION_PARSERS: dict[str, tuple[str, BriefSection | None]] = {
    "Built":                       ("elaborated", "built"),
    "Planned (committed)":         ("elaborated", "planned_committed"),
    "Planned (not yet committed)": ("bullet",     "planned_not_committed"),
    "Backlog":                     ("bullet",     "backlog"),
    "Archived":                    ("elaborated", "archived"),
    "Non-goals":                   ("non_goals",  None),
}


def parse_brief(text: str) -> ParsedBriefResult:
    """Parse a complete brief markdown into capabilities and non-goals.

    Raises BriefParseError on any format violation: missing anchors,
    duplicate IDs, alias collisions, AC referencing missing behaviors,
    etc. All format-rule errors surface here.
    """
    parsed = split_into_sections(text)

    capabilities: list[BriefCapability] = []
    non_goals: list[BriefNonGoal] = []

    for heading, body in parsed.sections.items():
        if heading not in _SECTION_PARSERS:
            # Unknown sections in the brief are ignored — this lets PO
            # add notes/scratch sections without breaking parse. If we
            # want strict mode later, flip this to BriefParseError.
            continue
        kind, section_label = _SECTION_PARSERS[heading]
        if not body.strip():
            continue
        if kind == "elaborated":
            capabilities.extend(parse_elaborated_section(body, section=section_label))
        elif kind == "bullet":
            capabilities.extend(parse_bullet_section(body, section=section_label))
        elif kind == "non_goals":
            non_goals.extend(parse_non_goals_section(body))

    _check_id_and_alias_uniqueness(capabilities, non_goals)

    return ParsedBriefResult(
        name=parsed.name,
        summary=parsed.summary,
        capabilities=capabilities,
        non_goals=non_goals,
    )


def _check_id_and_alias_uniqueness(
    capabilities: list[BriefCapability], non_goals: list[BriefNonGoal]
) -> None:
    """Format rule 5/6: IDs unique across the brief; aliases unique
    across the brief and don't collide with any id.

    Behavior IDs are namespaced per capability (already enforced when
    the parser populated each capability) so we don't check them across
    capabilities.
    """
    seen_ids: set[str] = set()
    seen_aliases: set[str] = set()

    def _add_id(scope: str, id_: str):
        if id_ in seen_ids:
            raise BriefParseError(
                f"duplicate id {id_!r} in {scope} (also defined elsewhere)"
            )
        if id_ in seen_aliases:
            raise BriefParseError(
                f"id {id_!r} in {scope} collides with an alias declared "
                "elsewhere"
            )
        seen_ids.add(id_)

    def _add_aliases(scope: str, aliases: list[str]):
        for a in aliases:
            if a in seen_ids:
                raise BriefParseError(
                    f"alias {a!r} in {scope} collides with an id declared "
                    "elsewhere"
                )
            if a in seen_aliases:
                raise BriefParseError(
                    f"duplicate alias {a!r} in {scope}"
                )
            seen_aliases.add(a)

    for c in capabilities:
        _add_id(f"capability {c.id!r}", c.id)
    for ng in non_goals:
        _add_id(f"non-goal {ng.id!r}", ng.id)
    for c in capabilities:
        _add_aliases(f"capability {c.id!r}", c.aliases)
    for ng in non_goals:
        _add_aliases(f"non-goal {ng.id!r}", ng.aliases)
```

- [ ] **Step 3: Run all brief-parser tests**

`uv run pytest tests/test_brief_parser.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add jig/brief_parser.py tests/test_brief_parser.py
git commit -m "feat(brief-parser): top-level parse_brief integrator + uniqueness checks"
```

---

## Phase 3: Regeneration merge

**What:** Merge a freshly-parsed brief with an existing `StructuredSpec` (if any), preserving operator-owned metadata (id, aliases, created_at, state_changed_at, tickets) and overwriting brief-derived content. Surface removed-from-brief capabilities as blocking gaps.

**Why:** Spec-gen needs deterministic regen so repeated runs preserve URI stability and timestamps.

**Verify:** `uv run pytest tests/test_spec_regeneration.py -v` passes.

### 3.1 Convert `BriefCapability` → `Capability` (first-time generation path)

**Files:**
- Create: `jig/spec_regeneration.py`
- Create: `tests/test_spec_regeneration.py`

- [ ] **Step 1: Write tests for first-time generation**

```python
# tests/test_spec_regeneration.py
from datetime import datetime, timezone

from jig.brief_parser import (
    BriefBehavior, BriefCapability, BriefNonGoal, BriefUserStory,
    ParsedBriefResult,
)
from jig.spec_regeneration import regenerate
from jig.spec_schema import CapabilityState, StructuredSpec


def _ts():
    return datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


def _empty_brief(**kwargs) -> ParsedBriefResult:
    base = dict(name="x", summary="y", capabilities=[], non_goals=[])
    base.update(kwargs)
    return ParsedBriefResult(**base)


def test_regenerate_first_time_creates_new_spec():
    brief = _empty_brief(
        name="todoapp",
        summary="A simple todo manager.",
        capabilities=[
            BriefCapability(
                id="due-dates",
                title="Due dates",
                section="planned_committed",
                summary="Users can set due dates",
                behaviors=[
                    BriefBehavior(
                        id="set-due-date",
                        description="Set a date",
                        acceptance_criteria=["A date can be set"],
                    ),
                ],
            ),
        ],
    )
    result = regenerate(
        brief=brief,
        existing=None,
        ticket_lookup=lambda cap_id, aliases: [],
        now=_ts(),
    )
    assert result.gaps == []
    spec = result.spec
    assert spec.name == "todoapp"
    assert len(spec.capabilities) == 1
    cap = spec.capabilities[0]
    assert cap.id == "due-dates"
    assert cap.state == CapabilityState.PLANNED
    assert cap.created_at == _ts()
    assert cap.last_updated == _ts()
    assert cap.state_changed_at == _ts()
    assert cap.behaviors[0].id == "set-due-date"
    assert cap.behaviors[0].acceptance_criteria == ["A date can be set"]
```

- [ ] **Step 2: Create the module skeleton**

```python
# jig/spec_regeneration.py
"""Merge a parsed brief with an existing StructuredSpec.

Preserves operator-owned metadata (id, aliases, created_at,
state_changed_at, tickets) for matched capabilities; assigns new
metadata to capabilities new in the brief; surfaces removed-from-brief
capabilities as blocking gaps for the operator to resolve.

Pure function: takes a ParsedBriefResult, an optional existing
StructuredSpec, and a ticket-lookup callback. Returns a
RegenerationResult containing either a new StructuredSpec or a list
of blocking gaps.

See ``docs/project-spec-schema/design.md`` §"Regeneration semantics".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from jig.brief_parser import (
    BriefBehavior, BriefCapability, BriefNonGoal, BriefUserStory,
    ParsedBriefResult,
)
from jig.spec_schema import (
    Behavior, Capability, CapabilityState, NonGoal, StructuredSpec, UserStory,
)


# Map BriefCapability.section → CapabilityState. in_progress is detected
# in Phase 4 by ticket-store inspection, so this map only handles the
# brief-section-to-base-state mapping.
_SECTION_TO_STATE: dict[str, CapabilityState] = {
    "built": CapabilityState.BUILT,
    "planned_committed": CapabilityState.PLANNED,
    "planned_not_committed": CapabilityState.PLANNED,
    "backlog": CapabilityState.BACKLOG,
    "archived": CapabilityState.ARCHIVED,
}


# Type alias: (capability_id, aliases) → list of ticket IDs.
TicketLookup = Callable[[str, list[str]], list[str]]


@dataclass
class RegenerationGap:
    """A blocking issue surfaced during regeneration."""
    kind: str             # 'removed_from_brief', 'ambiguous_alias', etc.
    location: str         # human-readable
    description: str
    severity: str = "blocking"
    suggested_question: str | None = None


@dataclass
class RegenerationResult:
    spec: StructuredSpec | None
    gaps: list[RegenerationGap] = field(default_factory=list)


def regenerate(
    *,
    brief: ParsedBriefResult,
    existing: StructuredSpec | None,
    ticket_lookup: TicketLookup,
    now: datetime,
) -> RegenerationResult:
    """Merge brief content with existing spec metadata. See module docstring."""
    capabilities, gaps = _merge_capabilities(brief, existing, ticket_lookup, now)
    non_goals, ng_gaps = _merge_non_goals(brief, existing)
    gaps.extend(ng_gaps)

    if gaps:
        # Any blocking gap aborts the publish — return gaps without a spec.
        if any(g.severity == "blocking" for g in gaps):
            return RegenerationResult(spec=None, gaps=gaps)

    spec = StructuredSpec(
        name=brief.name,
        summary=brief.summary,
        capabilities=capabilities,
        non_goals=non_goals,
        generated_at=now,
        spec_version=1,
    )
    return RegenerationResult(spec=spec, gaps=gaps)


def _to_capability_new(
    brief_cap: BriefCapability, *, now: datetime, tickets: list[str]
) -> Capability:
    return Capability(
        id=brief_cap.id,
        title=brief_cap.title,
        state=_SECTION_TO_STATE[brief_cap.section],
        summary=brief_cap.summary,
        user_story=_to_user_story(brief_cap.user_story),
        behaviors=[_to_behavior(b) for b in brief_cap.behaviors],
        acceptance_criteria=brief_cap.capability_acceptance_criteria,
        excluded=brief_cap.excluded,
        open_questions=brief_cap.open_questions,
        tickets=tickets,
        aliases=list(brief_cap.aliases),
        created_at=now,
        last_updated=now,
        state_changed_at=now,
    )


def _to_user_story(s: BriefUserStory | None) -> UserStory | None:
    if s is None:
        return None
    return UserStory(**{"as": s.as_, "want": s.want, "benefit": s.benefit})


def _to_behavior(b: BriefBehavior) -> Behavior:
    return Behavior(
        id=b.id,
        description=b.description,
        examples=b.examples,
        acceptance_criteria=b.acceptance_criteria,
    )


def _merge_capabilities(
    brief: ParsedBriefResult,
    existing: StructuredSpec | None,
    ticket_lookup: TicketLookup,
    now: datetime,
) -> tuple[list[Capability], list[RegenerationGap]]:
    out: list[Capability] = []
    matched_ids: set[str] = set()

    for brief_cap in brief.capabilities:
        match = None
        if existing is not None:
            match = existing.capability_by_id_or_alias(brief_cap.id)
            if match is None:
                for alias in brief_cap.aliases:
                    candidate = existing.capability_by_id_or_alias(alias)
                    if candidate is not None:
                        match = candidate
                        break
        tickets = ticket_lookup(brief_cap.id, brief_cap.aliases)
        if match is None:
            out.append(_to_capability_new(brief_cap, now=now, tickets=tickets))
        else:
            matched_ids.add(match.id)
            out.append(_merge_one(brief_cap, match, tickets=tickets, now=now))

    # Removed-from-brief gaps
    gaps: list[RegenerationGap] = []
    if existing is not None:
        for ec in existing.capabilities:
            if ec.id in matched_ids:
                continue
            gaps.append(RegenerationGap(
                kind="removed_from_brief",
                location=f"capability {ec.id!r} (state={ec.state.value})",
                description=(
                    f"capability {ec.id!r} is in structured.yaml but not in "
                    "the brief. Add it to a brief section (Built / Planned / "
                    "Backlog / Archived), or add it as an alias to another "
                    "capability if you renamed it."
                ),
            ))
    return out, gaps


def _merge_one(
    brief_cap: BriefCapability,
    match: Capability,
    *,
    tickets: list[str],
    now: datetime,
) -> Capability:
    new_state = _SECTION_TO_STATE[brief_cap.section]
    state_changed = match.state_changed_at if new_state == match.state else now
    return Capability(
        # Brief is source of truth for id + aliases (operator may rename via brief)
        id=brief_cap.id,
        aliases=list(brief_cap.aliases),
        title=brief_cap.title,
        state=new_state,
        summary=brief_cap.summary,
        user_story=_to_user_story(brief_cap.user_story),
        behaviors=[_to_behavior(b) for b in brief_cap.behaviors],
        acceptance_criteria=brief_cap.capability_acceptance_criteria,
        excluded=brief_cap.excluded,
        open_questions=brief_cap.open_questions,
        tickets=tickets,
        # Preserved
        created_at=match.created_at,
        # Recomputed
        last_updated=now,
        state_changed_at=state_changed,
    )


def _merge_non_goals(
    brief: ParsedBriefResult,
    existing: StructuredSpec | None,
) -> tuple[list[NonGoal], list[RegenerationGap]]:
    out: list[NonGoal] = []
    matched_ids: set[str] = set()
    for brief_ng in brief.non_goals:
        match = None
        if existing is not None:
            match = existing.non_goal_by_id_or_alias(brief_ng.id)
            if match is None:
                for alias in brief_ng.aliases:
                    candidate = existing.non_goal_by_id_or_alias(alias)
                    if candidate is not None:
                        match = candidate
                        break
        out.append(NonGoal(
            id=brief_ng.id,
            text=brief_ng.text,
            rationale=brief_ng.rationale,
            aliases=list(brief_ng.aliases),
        ))
        if match is not None:
            matched_ids.add(match.id)

    gaps: list[RegenerationGap] = []
    if existing is not None:
        for eng in existing.non_goals:
            if eng.id in matched_ids:
                continue
            gaps.append(RegenerationGap(
                kind="removed_from_brief",
                location=f"non-goal {eng.id!r}",
                description=(
                    f"non-goal {eng.id!r} is in structured.yaml but not in "
                    "the brief. Add it back to ## Non-goals or add it as an "
                    "alias to another non-goal if you renamed it."
                ),
            ))
    return out, gaps
```

- [ ] **Step 3: Run the first-time test**

`uv run pytest tests/test_spec_regeneration.py -v -k first_time`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add jig/spec_regeneration.py tests/test_spec_regeneration.py
git commit -m "feat(spec-regen): merge brief with optional existing spec; new-capability path"
```

### 3.2 Match-and-preserve: existing capabilities matched by ID

**Files:**
- Modify: `tests/test_spec_regeneration.py` (more tests)

- [ ] **Step 1: Write tests for the merge path**

```python
# tests/test_spec_regeneration.py
def test_regenerate_preserves_created_at_for_matched_id():
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(
                id="due-dates", title="Due dates",
                state=CapabilityState.PLANNED,
                acceptance_criteria=["a date can be set"],
                created_at=earlier, last_updated=earlier, state_changed_at=earlier,
            ),
        ],
        generated_at=earlier,
    )
    brief = _empty_brief(
        capabilities=[
            BriefCapability(
                id="due-dates",
                title="Due dates (revised)",
                section="planned_committed",
                capability_acceptance_criteria=["a date can be set"],
            ),
        ],
    )
    result = regenerate(
        brief=brief, existing=existing,
        ticket_lookup=lambda cid, aliases: [], now=_ts(),
    )
    assert result.spec is not None
    cap = result.spec.capabilities[0]
    assert cap.created_at == earlier             # preserved
    assert cap.last_updated == _ts()             # bumped
    assert cap.title == "Due dates (revised)"    # overwritten


def test_regenerate_bumps_state_changed_at_when_state_changes():
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(
                id="due-dates", title="Due dates",
                state=CapabilityState.PLANNED,
                acceptance_criteria=["a date can be set"],
                created_at=earlier, last_updated=earlier, state_changed_at=earlier,
            ),
        ],
        generated_at=earlier,
    )
    brief = _empty_brief(
        capabilities=[
            BriefCapability(
                id="due-dates", title="Due dates",
                section="built",  # was planned, now built
            ),
        ],
    )
    result = regenerate(
        brief=brief, existing=existing,
        ticket_lookup=lambda cid, aliases: [], now=_ts(),
    )
    cap = result.spec.capabilities[0]
    assert cap.state == CapabilityState.BUILT
    assert cap.state_changed_at == _ts()


def test_regenerate_matches_via_brief_alias():
    """Brief's anchor declares aliases; existing has the alias as id —
    rename detected, existing entry merged into brief's new id."""
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(
                id="due-dates", title="Due dates",
                state=CapabilityState.PLANNED,
                acceptance_criteria=["x"],
                created_at=earlier, last_updated=earlier, state_changed_at=earlier,
            ),
        ],
        generated_at=earlier,
    )
    brief = _empty_brief(
        capabilities=[
            BriefCapability(
                id="deadlines", aliases=["due-dates"],
                title="Deadlines", section="planned_committed",
                capability_acceptance_criteria=["x"],
            ),
        ],
    )
    result = regenerate(
        brief=brief, existing=existing,
        ticket_lookup=lambda cid, aliases: [], now=_ts(),
    )
    cap = result.spec.capabilities[0]
    assert cap.id == "deadlines"           # renamed per brief
    assert cap.aliases == ["due-dates"]    # alias kept (operator declared it)
    assert cap.created_at == earlier       # original timestamp preserved
```

- [ ] **Step 2: Run tests; the merge path is already implemented in §3.1**

`uv run pytest tests/test_spec_regeneration.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_spec_regeneration.py
git commit -m "test(spec-regen): match-by-id, match-by-alias, state transitions"
```

### 3.3 Removed-from-brief gap

**Files:**
- Modify: `tests/test_spec_regeneration.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_spec_regeneration.py
def test_regenerate_surfaces_removed_capability_as_gap():
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(
                id="dropped-feature", title="Dropped feature",
                state=CapabilityState.PLANNED,
                acceptance_criteria=["x"],
                created_at=earlier, last_updated=earlier, state_changed_at=earlier,
            ),
        ],
        generated_at=earlier,
    )
    brief = _empty_brief()  # no capabilities at all
    result = regenerate(
        brief=brief, existing=existing,
        ticket_lookup=lambda cid, aliases: [], now=_ts(),
    )
    assert result.spec is None
    assert len(result.gaps) == 1
    assert result.gaps[0].kind == "removed_from_brief"
    assert "dropped-feature" in result.gaps[0].location
```

- [ ] **Step 2: Run; the path is implemented in §3.1**

`uv run pytest tests/test_spec_regeneration.py::test_regenerate_surfaces_removed_capability_as_gap -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_spec_regeneration.py
git commit -m "test(spec-regen): removed-from-brief surfaces as blocking gap"
```

### 3.4 Ticket linkage rebuild

**Files:**
- Modify: `tests/test_spec_regeneration.py`

- [ ] **Step 1: Write a test demonstrating ticket_lookup wiring**

```python
# tests/test_spec_regeneration.py
def test_regenerate_populates_capability_tickets_from_lookup():
    """`ticket_lookup` is called per capability; spec-gen wires it
    against the ticket store at integration time."""
    brief = _empty_brief(
        capabilities=[
            BriefCapability(
                id="due-dates", title="Due dates",
                section="planned_committed",
                capability_acceptance_criteria=["x"],
            ),
        ],
    )

    def lookup(cap_id: str, aliases: list[str]) -> list[str]:
        if cap_id == "due-dates":
            return ["ticket-42", "ticket-43"]
        return []

    result = regenerate(
        brief=brief, existing=None,
        ticket_lookup=lookup, now=_ts(),
    )
    assert result.spec.capabilities[0].tickets == ["ticket-42", "ticket-43"]
```

- [ ] **Step 2: Run; already implemented**

`uv run pytest tests/test_spec_regeneration.py -v`
Expected: PASS (all regen tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_spec_regeneration.py
git commit -m "test(spec-regen): ticket_lookup callback populates Capability.tickets"
```

---

## Phase 4: URI resolver

**What:** Extend `jig/context_resolver.py` with a `project://spec/...` branch that resolves capability/behavior/non-goal/state collection URIs to structured data and rendered text.

**Why:** Decision records and ticket links reference spec content via URIs; resolution must be available at context-bundle time.

**Verify:** `uv run pytest tests/test_spec_uri_resolver.py -v` passes; existing context-resolver tests still pass.

### 4.1 Define the URI parsing helper

**Files:**
- Create: `jig/spec_uri.py`
- Create: `tests/test_spec_uri_resolver.py`

- [ ] **Step 1: Write tests for URI parsing**

```python
# tests/test_spec_uri_resolver.py
import pytest

from jig.spec_uri import parse_spec_uri, SpecUri, SpecUriError


def test_parse_spec_uri_root():
    p = parse_spec_uri("project://spec")
    assert p == SpecUri(parts=[], fragment=None)


def test_parse_spec_uri_capability_by_id():
    p = parse_spec_uri("project://spec/capabilities/due-dates")
    assert p == SpecUri(parts=["capabilities", "due-dates"], fragment=None)


def test_parse_spec_uri_capability_with_behavior_fragment():
    p = parse_spec_uri("project://spec/capabilities/due-dates#sort-by-due-date")
    assert p.parts == ["capabilities", "due-dates"]
    assert p.fragment == "sort-by-due-date"


def test_parse_spec_uri_state_collection():
    p = parse_spec_uri("project://spec/state/planned")
    assert p.parts == ["state", "planned"]


def test_parse_spec_uri_non_goal_by_id():
    p = parse_spec_uri("project://spec/non-goals/no-multi-user")
    assert p.parts == ["non-goals", "no-multi-user"]


def test_parse_spec_uri_rejects_relative():
    with pytest.raises(SpecUriError, match="prefix"):
        parse_spec_uri("capabilities/due-dates")


def test_parse_spec_uri_rejects_other_scheme():
    with pytest.raises(SpecUriError, match="prefix"):
        parse_spec_uri("ticket://abc")


def test_parse_spec_uri_treats_empty_fragment_as_no_fragment():
    p = parse_spec_uri("project://spec/capabilities/x#")
    assert p.fragment is None
```

- [ ] **Step 2: Implement URI parsing**

```python
# jig/spec_uri.py
"""Parser for ``project://spec/...`` URIs.

Strict prefix — relative URIs are rejected. Empty fragment is treated
as no fragment.

See ``docs/project-spec-schema/design.md`` §"URI scheme".
"""
from __future__ import annotations

from dataclasses import dataclass


_PREFIX = "project://spec"


class SpecUriError(ValueError):
    """Raised when a project://spec/... URI is malformed."""


@dataclass
class SpecUri:
    """Parsed URI: parts after the prefix, plus optional fragment."""
    parts: list[str]
    fragment: str | None


def parse_spec_uri(uri: str) -> SpecUri:
    """Parse a ``project://spec/...`` URI.

    Returns SpecUri(parts=[after-prefix-segments], fragment=...).
    Empty fragment → None. Raises SpecUriError if not a project://spec
    URI or the path is malformed.
    """
    if not uri.startswith(_PREFIX):
        raise SpecUriError(
            f"URI must start with {_PREFIX!r}; got {uri!r}"
        )
    rest = uri[len(_PREFIX):]
    if rest and not rest.startswith("/"):
        raise SpecUriError(
            f"expected '/' or end after {_PREFIX!r}; got {uri!r}"
        )
    rest = rest.lstrip("/")
    fragment: str | None = None
    if "#" in rest:
        path_part, _, frag = rest.partition("#")
        rest = path_part
        if frag:
            fragment = frag
    parts = [p for p in rest.split("/") if p]
    return SpecUri(parts=parts, fragment=fragment)
```

- [ ] **Step 3: Run tests**

`uv run pytest tests/test_spec_uri_resolver.py -v -k parse_spec_uri`
Expected: 8 PASS.

- [ ] **Step 4: Commit**

```bash
git add jig/spec_uri.py tests/test_spec_uri_resolver.py
git commit -m "feat(spec-uri): strict project://spec/... URI parser"
```

### 4.2 Resolver: parts → structured data

**Files:**
- Modify: `jig/spec_uri.py`
- Modify: `tests/test_spec_uri_resolver.py`

- [ ] **Step 1: Write resolution tests**

```python
# tests/test_spec_uri_resolver.py
from datetime import datetime, timezone

from jig.spec_schema import (
    Behavior, Capability, CapabilityState, NonGoal, StructuredSpec,
)
from jig.spec_uri import resolve_spec_uri, SpecUriError


def _ts():
    return datetime(2026, 4, 27, tzinfo=timezone.utc)


def _spec() -> StructuredSpec:
    return StructuredSpec(
        name="todoapp",
        summary="A simple todo manager.",
        capabilities=[
            Capability(
                id="due-dates", title="Due dates",
                state=CapabilityState.PLANNED,
                behaviors=[
                    Behavior(id="set-due-date", description="x",
                             acceptance_criteria=["a"]),
                ],
                created_at=_ts(), last_updated=_ts(), state_changed_at=_ts(),
            ),
            Capability(
                id="priorities", title="Priorities",
                state=CapabilityState.BACKLOG,
                created_at=_ts(), last_updated=_ts(), state_changed_at=_ts(),
            ),
        ],
        non_goals=[NonGoal(id="no-multi-user", text="Multi-user")],
        generated_at=_ts(),
    )


def test_resolve_root_returns_full_spec():
    out = resolve_spec_uri("project://spec", _spec())
    assert out["kind"] == "spec"
    assert out["data"]["name"] == "todoapp"


def test_resolve_capability_by_id():
    out = resolve_spec_uri("project://spec/capabilities/due-dates", _spec())
    assert out["kind"] == "capability"
    assert out["data"]["id"] == "due-dates"


def test_resolve_capability_via_alias():
    spec = _spec()
    spec.capabilities[0].aliases = ["dd"]
    out = resolve_spec_uri("project://spec/capabilities/dd", spec)
    assert out["data"]["id"] == "due-dates"


def test_resolve_unknown_capability_raises():
    with pytest.raises(SpecUriError, match="capability"):
        resolve_spec_uri("project://spec/capabilities/nope", _spec())


def test_resolve_capability_behavior_fragment():
    out = resolve_spec_uri(
        "project://spec/capabilities/due-dates#set-due-date", _spec()
    )
    assert out["kind"] == "behavior"
    assert out["data"]["id"] == "set-due-date"


def test_resolve_unknown_behavior_fragment_raises():
    with pytest.raises(SpecUriError, match="behavior"):
        resolve_spec_uri(
            "project://spec/capabilities/due-dates#nope", _spec()
        )


def test_resolve_non_goal_by_id():
    out = resolve_spec_uri("project://spec/non-goals/no-multi-user", _spec())
    assert out["kind"] == "non_goal"
    assert out["data"]["text"] == "Multi-user"


def test_resolve_state_collection():
    out = resolve_spec_uri("project://spec/state/planned", _spec())
    assert out["kind"] == "capability_list"
    ids = [c["id"] for c in out["data"]]
    assert ids == ["due-dates"]


def test_resolve_unknown_state_raises():
    with pytest.raises(SpecUriError, match="state"):
        resolve_spec_uri("project://spec/state/bogus", _spec())
```

- [ ] **Step 2: Implement the resolver**

```python
# jig/spec_uri.py — append
from typing import Any

from jig.spec_schema import (
    Behavior, Capability, CapabilityState, NonGoal, StructuredSpec,
)


def resolve_spec_uri(uri: str, spec: StructuredSpec) -> dict[str, Any]:
    """Resolve a ``project://spec/...`` URI against the given spec.

    Returns a ``{kind, data}`` dict where ``kind`` is one of
    ``spec / capability / behavior / non_goal / capability_list / name / summary``
    and ``data`` is the structured value (model_dump'd dict for objects;
    raw value for primitives).

    Raises SpecUriError on unknown ids, unknown fragments, unknown
    state collections.
    """
    parsed = parse_spec_uri(uri)
    parts = parsed.parts

    if not parts:
        return {"kind": "spec", "data": spec.model_dump(mode="json", by_alias=True)}

    head = parts[0]

    if head == "name" and len(parts) == 1:
        return {"kind": "name", "data": spec.name}

    if head == "summary" and len(parts) == 1:
        return {"kind": "summary", "data": spec.summary}

    if head == "capabilities":
        if len(parts) == 1:
            return {
                "kind": "capability_list",
                "data": [c.model_dump(mode="json", by_alias=True)
                         for c in spec.capabilities],
            }
        cap_id = parts[1]
        cap = spec.capability_by_id_or_alias(cap_id)
        if cap is None:
            raise SpecUriError(
                f"capability {cap_id!r} not found in spec"
            )
        if parsed.fragment is None:
            return {
                "kind": "capability",
                "data": cap.model_dump(mode="json", by_alias=True),
            }
        for b in cap.behaviors:
            if b.id == parsed.fragment:
                return {
                    "kind": "behavior",
                    "data": b.model_dump(mode="json"),
                }
        raise SpecUriError(
            f"behavior {parsed.fragment!r} not found in capability {cap_id!r}"
        )

    if head == "non-goals":
        if len(parts) == 1:
            return {
                "kind": "non_goal_list",
                "data": [n.model_dump(mode="json") for n in spec.non_goals],
            }
        ng_id = parts[1]
        ng = spec.non_goal_by_id_or_alias(ng_id)
        if ng is None:
            raise SpecUriError(f"non-goal {ng_id!r} not found in spec")
        return {"kind": "non_goal", "data": ng.model_dump(mode="json")}

    if head == "state":
        if len(parts) != 2:
            raise SpecUriError(
                "state URI requires a single state name segment"
            )
        try:
            state = CapabilityState(parts[1])
        except ValueError:
            raise SpecUriError(
                f"unknown state {parts[1]!r}; expected one of "
                f"{sorted(s.value for s in CapabilityState)}"
            )
        return {
            "kind": "capability_list",
            "data": [c.model_dump(mode="json", by_alias=True)
                     for c in spec.capabilities if c.state == state],
        }

    raise SpecUriError(f"unrecognized spec URI path: {parts!r}")
```

- [ ] **Step 3: Run resolver tests**

`uv run pytest tests/test_spec_uri_resolver.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add jig/spec_uri.py tests/test_spec_uri_resolver.py
git commit -m "feat(spec-uri): resolve project://spec/... to structured data"
```

### 4.3 Wire `project://spec/...` into context_resolver

**Files:**
- Modify: `jig/context_resolver.py:374-388` (`_resolve_project`)
- Modify: existing `tests/test_context_resolver.py` if present

- [ ] **Step 1: Read the current `_resolve_project` function**

`grep -n "_resolve_project" jig/context_resolver.py`
Note line numbers; the new branch goes before the file-reading fallback so `project://spec/...` is intercepted first.

- [ ] **Step 2: Write a test that resolves a spec URI through the context resolver**

```python
# tests/test_context_resolver.py — add at end (or create file with imports)
import pytest
import yaml

from jig.context_resolver import _resolve_project  # type: ignore[attr-defined]
from jig.spec_schema import (
    Capability, CapabilityState, StructuredSpec,
)
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_resolve_project_spec_capability_via_context_resolver(tmp_path):
    spec_path = tmp_path / ".jig" / "spec"
    spec_path.mkdir(parents=True)
    spec = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(
                id="due-dates", title="Due dates",
                state=CapabilityState.PLANNED,
                acceptance_criteria=["x"],
                created_at=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
                state_changed_at=datetime.now(timezone.utc),
            ),
        ],
        generated_at=datetime.now(timezone.utc),
    )
    (spec_path / "project.structured.yaml").write_text(
        yaml.safe_dump(spec.model_dump(mode="json", by_alias=True))
    )
    out = await _resolve_project(
        body="spec/capabilities/due-dates",
        ticket=None, parent=None, threads=None,
        worktree_path=tmp_path, project_path=tmp_path,
    )
    assert "due-dates" in out
    assert "Due dates" in out
```

- [ ] **Step 3: Add the spec-URI branch to `_resolve_project`**

```python
# jig/context_resolver.py — modify _resolve_project
import yaml

from jig.spec_schema import StructuredSpec
from jig.spec_uri import resolve_spec_uri, SpecUriError


async def _resolve_project(
    body: str,
    *,
    ticket,                       # types unchanged from existing signature
    parent,
    threads,
    worktree_path: "Path",
    project_path: "Path",
) -> str:
    # NEW: spec/... routes through the structured-spec resolver.
    if body.startswith("spec") and (body == "spec" or body.startswith("spec/") or body.startswith("spec#")):
        spec_file = project_path / ".jig" / "spec" / "project.structured.yaml"
        if not spec_file.is_file():
            return f"# project://{body}\n\n(no project.structured.yaml exists yet)\n"
        data = yaml.safe_load(spec_file.read_text()) or {}
        spec = StructuredSpec.model_validate(data)
        try:
            out = resolve_spec_uri(f"project://{body}", spec)
        except SpecUriError as e:
            return f"# project://{body}\n\n[unresolved: {e}]\n"
        # Render the structured payload as YAML text for context-bundle injection.
        return yaml.safe_dump(out["data"], sort_keys=False)

    # EXISTING: file-based project context (unchanged)
    base = project_path / ".jig" / "context" / "project"
    return _read_context_file(base, body, f"project://{body}")
```

- [ ] **Step 4: Run the new test**

`uv run pytest tests/test_context_resolver.py -v -k spec_capability_via`
Expected: PASS.

- [ ] **Step 5: Run the broader test suite**

`uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add jig/context_resolver.py tests/test_context_resolver.py
git commit -m "feat(context-resolver): handle project://spec/... URIs"
```

---

## Phase 5: MCP tools

**What:** Add capability-aware MCP tools (`spec_list_capabilities`, `spec_get_capability`, `spec_get_behavior`, `spec_list_non_goals`, `spec_get_non_goal`, `spec_resolve_uri`) and the spec-gen-only `spec_load_existing`. Keep generic `spec_get_field` / `spec_list_fields`. Update SA's `allowed_tools`.

**Why:** Downstream agents query at the domain level instead of dot-paths.

**Verify:** `tests/test_init_mcp_spec.py` and a new `tests/test_init_mcp_spec_query.py` pass; SA's spawn config picks up the new tools.

### 5.1 Handler functions in `init_mcp.py`

**Files:**
- Modify: `jig/init_mcp.py`
- Create: `tests/test_init_mcp_spec_query.py`

- [ ] **Step 1: Write tests for the new query handlers**

```python
# tests/test_init_mcp_spec_query.py
from datetime import datetime, timezone

import pytest
import yaml

from jig.init_mcp import (
    handle_spec_list_capabilities,
    handle_spec_get_capability,
    handle_spec_get_behavior,
    handle_spec_list_non_goals,
    handle_spec_get_non_goal,
    handle_spec_resolve_uri,
    handle_spec_load_existing,
)
from jig.spec_schema import (
    Behavior, Capability, CapabilityState, NonGoal, StructuredSpec,
)


def _ts():
    return datetime(2026, 4, 27, tzinfo=timezone.utc)


@pytest.fixture
def spec_path(tmp_path):
    spec = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(
                id="due-dates", title="Due dates",
                state=CapabilityState.PLANNED,
                behaviors=[Behavior(id="b1", description="x",
                                    acceptance_criteria=["a"])],
                created_at=_ts(), last_updated=_ts(), state_changed_at=_ts(),
            ),
            Capability(
                id="priorities", title="Priorities",
                state=CapabilityState.BACKLOG,
                created_at=_ts(), last_updated=_ts(), state_changed_at=_ts(),
            ),
        ],
        non_goals=[NonGoal(id="no-multi-user", text="Multi-user")],
        generated_at=_ts(),
    )
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.structured.yaml").write_text(
        yaml.safe_dump(spec.model_dump(mode="json", by_alias=True))
    )
    return tmp_path


@pytest.mark.asyncio
async def test_spec_list_capabilities_returns_summary(spec_path):
    out = await handle_spec_list_capabilities(
        project_path=spec_path, state=None,
    )
    assert {c["id"] for c in out} == {"due-dates", "priorities"}
    assert all({"id", "title", "state"} <= c.keys() for c in out)


@pytest.mark.asyncio
async def test_spec_list_capabilities_filters_by_state(spec_path):
    out = await handle_spec_list_capabilities(
        project_path=spec_path, state="planned",
    )
    assert [c["id"] for c in out] == ["due-dates"]


@pytest.mark.asyncio
async def test_spec_get_capability_returns_full_object(spec_path):
    out = await handle_spec_get_capability(
        project_path=spec_path, id="due-dates",
    )
    assert out["id"] == "due-dates"
    assert len(out["behaviors"]) == 1


@pytest.mark.asyncio
async def test_spec_get_capability_raises_on_unknown(spec_path):
    with pytest.raises(KeyError):
        await handle_spec_get_capability(project_path=spec_path, id="nope")


@pytest.mark.asyncio
async def test_spec_get_behavior(spec_path):
    out = await handle_spec_get_behavior(
        project_path=spec_path, capability_id="due-dates", behavior_id="b1",
    )
    assert out["id"] == "b1"


@pytest.mark.asyncio
async def test_spec_list_non_goals(spec_path):
    out = await handle_spec_list_non_goals(project_path=spec_path)
    assert [n["id"] for n in out] == ["no-multi-user"]


@pytest.mark.asyncio
async def test_spec_get_non_goal(spec_path):
    out = await handle_spec_get_non_goal(
        project_path=spec_path, id="no-multi-user",
    )
    assert out["text"] == "Multi-user"


@pytest.mark.asyncio
async def test_spec_resolve_uri_capability(spec_path):
    out = await handle_spec_resolve_uri(
        project_path=spec_path,
        uri="project://spec/capabilities/due-dates",
    )
    assert out["kind"] == "capability"


@pytest.mark.asyncio
async def test_spec_load_existing_returns_dict(spec_path):
    out = await handle_spec_load_existing(project_path=spec_path)
    assert out["name"] == "x"
    assert len(out["capabilities"]) == 2


@pytest.mark.asyncio
async def test_spec_load_existing_empty_when_no_spec(tmp_path):
    out = await handle_spec_load_existing(project_path=tmp_path)
    assert out == {}
```

- [ ] **Step 2: Implement the handlers in `init_mcp.py`**

```python
# jig/init_mcp.py — add
from jig.spec_schema import StructuredSpec
from jig.spec_uri import resolve_spec_uri, SpecUriError


def _load_spec(project_path: Path) -> StructuredSpec | None:
    """Load and validate the structured spec; None if absent."""
    spec_file = _spec_path(project_path)
    if not spec_file.is_file():
        return None
    data = yaml.safe_load(spec_file.read_text()) or {}
    return StructuredSpec.model_validate(data)


async def handle_spec_load_existing(*, project_path: Path) -> dict[str, Any]:
    """Return the existing structured spec as a dict, or {} if absent.

    Spec-gen calls this at the top of every regen run.
    """
    spec = _load_spec(project_path)
    if spec is None:
        return {}
    return spec.model_dump(mode="json", by_alias=True)


async def handle_spec_list_capabilities(
    *, project_path: Path, state: str | None = None,
) -> list[dict[str, Any]]:
    spec = _load_spec(project_path)
    if spec is None:
        return []
    out = [
        {"id": c.id, "title": c.title, "state": c.state.value}
        for c in spec.capabilities
        if state is None or c.state.value == state
    ]
    return out


async def handle_spec_get_capability(
    *, project_path: Path, id: str,
) -> dict[str, Any]:
    spec = _load_spec(project_path)
    if spec is None:
        raise KeyError(f"no spec yet; cannot get capability {id!r}")
    cap = spec.capability_by_id_or_alias(id)
    if cap is None:
        raise KeyError(f"capability {id!r} not found")
    return cap.model_dump(mode="json", by_alias=True)


async def handle_spec_get_behavior(
    *, project_path: Path, capability_id: str, behavior_id: str,
) -> dict[str, Any]:
    cap_data = await handle_spec_get_capability(
        project_path=project_path, id=capability_id,
    )
    for b in cap_data.get("behaviors", []):
        if b["id"] == behavior_id:
            return b
    raise KeyError(
        f"behavior {behavior_id!r} not found in capability {capability_id!r}"
    )


async def handle_spec_list_non_goals(
    *, project_path: Path,
) -> list[dict[str, Any]]:
    spec = _load_spec(project_path)
    if spec is None:
        return []
    return [
        {"id": n.id, "text": n.text, "rationale": n.rationale}
        for n in spec.non_goals
    ]


async def handle_spec_get_non_goal(
    *, project_path: Path, id: str,
) -> dict[str, Any]:
    spec = _load_spec(project_path)
    if spec is None:
        raise KeyError(f"no spec yet; cannot get non-goal {id!r}")
    ng = spec.non_goal_by_id_or_alias(id)
    if ng is None:
        raise KeyError(f"non-goal {id!r} not found")
    return ng.model_dump(mode="json")


async def handle_spec_resolve_uri(
    *, project_path: Path, uri: str,
) -> dict[str, Any]:
    spec = _load_spec(project_path)
    if spec is None:
        raise KeyError(f"no spec yet; cannot resolve {uri!r}")
    try:
        return resolve_spec_uri(uri, spec)
    except SpecUriError as e:
        raise KeyError(str(e)) from e
```

- [ ] **Step 3: Run handler tests**

`uv run pytest tests/test_init_mcp_spec_query.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add jig/init_mcp.py tests/test_init_mcp_spec_query.py
git commit -m "feat(spec-mcp): capability-aware query handlers + spec_load_existing"
```

### 5.2 Register tools in `mcp_server.py`

**Files:**
- Modify: `jig/mcp_server.py`
- Modify: `tests/test_init_mcp_registration.py`

- [ ] **Step 1: Write a test that the new tools register for SA**

```python
# tests/test_init_mcp_registration.py — add
@pytest.mark.asyncio
async def test_sa_server_registers_capability_aware_tools(
    tmp_path, stores, monkeypatch,
):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="sa",
        allowed_tools=[
            "spec_list_capabilities",
            "spec_get_capability",
            "spec_get_behavior",
            "spec_list_non_goals",
            "spec_get_non_goal",
            "spec_resolve_uri",
            "spec_load_existing",
        ],
        strict_tools=True,
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets, threads=threads, memory=memory, bus=bus,
        agent_role="sa", agent_cfg=cfg,
        worktree_path=tmp_path, project_path=tmp_path,
    )
    names = _tool_names(captured)
    assert {
        "spec_list_capabilities",
        "spec_get_capability",
        "spec_get_behavior",
        "spec_list_non_goals",
        "spec_get_non_goal",
        "spec_resolve_uri",
        "spec_load_existing",
    } == names
```

- [ ] **Step 2: Add tool registrations**

```python
# jig/mcp_server.py — add (in the same cluster as spec_get_field, after it)

    if "spec_list_capabilities" in agent_cfg.allowed_tools:

        @tool(
            "spec_list_capabilities",
            "List capabilities in the project spec. Optional `state` filter "
            "('backlog', 'planned', 'in_progress', 'built', 'archived'). "
            "Returns id, title, state for each — lightweight summary; "
            "use `spec_get_capability` for full content.",
            {"state": str},
        )
        async def spec_list_capabilities(args):
            out = await init_mcp.handle_spec_list_capabilities(
                project_path=project_path,
                state=args.get("state"),
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_list_capabilities)

    if "spec_get_capability" in agent_cfg.allowed_tools:

        @tool(
            "spec_get_capability",
            "Get a full Capability by id (or alias). Returns id, title, "
            "state, summary, user_story, behaviors (each with description, "
            "examples, acceptance_criteria), capability-level "
            "acceptance_criteria, excluded, open_questions, tickets, aliases.",
            {"id": str},
        )
        async def spec_get_capability(args):
            out = await init_mcp.handle_spec_get_capability(
                project_path=project_path, id=args["id"],
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_get_capability)

    if "spec_get_behavior" in agent_cfg.allowed_tools:

        @tool(
            "spec_get_behavior",
            "Get a full Behavior by capability_id + behavior_id.",
            {"capability_id": str, "behavior_id": str},
        )
        async def spec_get_behavior(args):
            out = await init_mcp.handle_spec_get_behavior(
                project_path=project_path,
                capability_id=args["capability_id"],
                behavior_id=args["behavior_id"],
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_get_behavior)

    if "spec_list_non_goals" in agent_cfg.allowed_tools:

        @tool(
            "spec_list_non_goals",
            "List all non-goals from the project spec. Returns id, text, "
            "rationale for each.",
            {},
        )
        async def spec_list_non_goals(args):
            out = await init_mcp.handle_spec_list_non_goals(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_list_non_goals)

    if "spec_get_non_goal" in agent_cfg.allowed_tools:

        @tool(
            "spec_get_non_goal",
            "Get a full NonGoal by id (or alias).",
            {"id": str},
        )
        async def spec_get_non_goal(args):
            out = await init_mcp.handle_spec_get_non_goal(
                project_path=project_path, id=args["id"],
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_get_non_goal)

    if "spec_resolve_uri" in agent_cfg.allowed_tools:

        @tool(
            "spec_resolve_uri",
            "Resolve a project://spec/... URI to structured data. Supports "
            "capability ids, behavior fragments (#behavior-id), non-goal "
            "ids, and state collections (project://spec/state/<state>).",
            {"uri": str},
        )
        async def spec_resolve_uri(args):
            out = await init_mcp.handle_spec_resolve_uri(
                project_path=project_path, uri=args["uri"],
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_resolve_uri)

    if "spec_load_existing" in agent_cfg.allowed_tools:

        @tool(
            "spec_load_existing",
            "Read the current structured spec as-is for the regen merge. "
            "Returns {} if no spec exists yet (first-time generation). "
            "Spec-generator only.",
            {},
        )
        async def spec_load_existing(args):
            out = await init_mcp.handle_spec_load_existing(
                project_path=project_path,
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_load_existing)
```

- [ ] **Step 3: Run registration tests**

`uv run pytest tests/test_init_mcp_registration.py -v`
Expected: all PASS.

- [ ] **Step 4: Run full suite**

`uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/mcp_server.py tests/test_init_mcp_registration.py
git commit -m "feat(spec-mcp): register capability-aware tools + spec_load_existing"
```

### 5.3 High-level `spec_generate_from_brief` tool

**Files:**
- Modify: `jig/init_mcp.py`
- Modify: `jig/mcp_server.py`
- Modify: `tests/test_init_mcp_spec_query.py`

**Why this exists:** Brief parsing + regen merge are deterministic Python. The spec-gen LLM's job is semantic judgment (looking for contradictions / ambiguities the parser can't catch), not running the algorithm by hand. This tool collapses parse + load-existing + merge into one MCP call so the agent's role stays thin.

- [ ] **Step 1: Write tests for the new handler**

```python
# tests/test_init_mcp_spec_query.py — add at end
@pytest.mark.asyncio
async def test_spec_generate_from_brief_first_time(tmp_path):
    """First-time generation: no existing spec, brief produces a fresh
    StructuredSpec dict."""
    from jig.init_mcp import handle_spec_generate_from_brief
    from jig.store.tickets import TicketStore

    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.md").write_text(
        "# todoapp\n\nA simple todo manager.\n\n"
        "## Backlog\n\n- {#mobile-app} Mobile app\n"
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()

    result = await handle_spec_generate_from_brief(
        project_path=tmp_path, tickets=tickets,
    )
    assert result["gaps"] == []
    spec = result["spec"]
    assert spec is not None
    assert spec["name"] == "todoapp"
    assert len(spec["capabilities"]) == 1
    assert spec["capabilities"][0]["id"] == "mobile-app"


@pytest.mark.asyncio
async def test_spec_generate_from_brief_returns_format_gaps(tmp_path):
    """Brief with a format violation surfaces as blocking gaps."""
    from jig.init_mcp import handle_spec_generate_from_brief
    from jig.store.tickets import TicketStore

    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.md").write_text(
        "# x\n\nintro\n\n## Backlog\n\n- Mobile app\n"  # missing {#id}
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()

    result = await handle_spec_generate_from_brief(
        project_path=tmp_path, tickets=tickets,
    )
    assert result["spec"] is None
    assert len(result["gaps"]) >= 1
    assert any(g["kind"] == "format_error" for g in result["gaps"])


@pytest.mark.asyncio
async def test_spec_generate_from_brief_preserves_metadata_on_regen(tmp_path):
    """Existing spec's created_at survives regen."""
    from datetime import datetime, timezone
    from jig.init_mcp import handle_spec_generate_from_brief
    from jig.store.tickets import TicketStore
    from jig.spec_schema import (
        Capability, CapabilityState, StructuredSpec,
    )
    import yaml

    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(
                id="mobile-app", title="Mobile app",
                state=CapabilityState.BACKLOG,
                created_at=earlier, last_updated=earlier,
                state_changed_at=earlier,
            ),
        ],
        generated_at=earlier,
    )
    (spec_dir / "project.structured.yaml").write_text(
        yaml.safe_dump(existing.model_dump(mode="json", by_alias=True))
    )
    (spec_dir / "project.md").write_text(
        "# x\n\nintro\n\n## Backlog\n\n- {#mobile-app} Mobile app\n"
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()

    result = await handle_spec_generate_from_brief(
        project_path=tmp_path, tickets=tickets,
    )
    spec = result["spec"]
    assert spec is not None
    assert spec["capabilities"][0]["created_at"] == earlier.isoformat()
```

- [ ] **Step 2: Implement the handler**

```python
# jig/init_mcp.py — add (near the other handle_spec_* functions)
from datetime import datetime, timezone

from jig.brief_parser import BriefParseError, parse_brief
from jig.spec_regeneration import RegenerationGap, regenerate
from jig.store.tickets import TicketStore


async def handle_spec_generate_from_brief(
    *, project_path: Path, tickets: TicketStore,
) -> dict[str, Any]:
    """Run the full spec-generation pipeline against the current brief
    and existing structured spec.

    Returns ``{"spec": dict | None, "gaps": [...]}``:
      - On format violations or removed-from-brief: ``spec=None`` and
        a list of gap dicts with ``severity="blocking"``.
      - On success: ``spec`` is the merged StructuredSpec as a dict
        (ready to pass to ``spec_publish``); ``gaps`` may contain
        advisory entries for the operator.

    Spec-gen's MCP-callable orchestration entry point — collapses what
    used to be parse + load_existing + merge + validate into one call.
    """
    brief_path = _brief_path(project_path)
    if not brief_path.is_file():
        return {
            "spec": None,
            "gaps": [{
                "kind": "missing",
                "location": str(brief_path),
                "description": "no project.md found",
                "severity": "blocking",
            }],
        }

    try:
        parsed_brief = parse_brief(brief_path.read_text())
    except BriefParseError as e:
        return {
            "spec": None,
            "gaps": [{
                "kind": "format_error",
                "location": "project.md",
                "description": str(e),
                "severity": "blocking",
            }],
        }

    existing = _load_spec(project_path)

    # Build the ticket-lookup callback over the live ticket store.
    # Tickets reference capabilities via Ticket.derived_from
    # (project://spec/capabilities/<id>); we collect by id and any alias.
    async def _all_tickets():
        return await tickets.list_all()

    ticket_records = await _all_tickets()

    def lookup(cap_id: str, aliases: list[str]) -> list[str]:
        wanted = {f"project://spec/capabilities/{cap_id}"}
        wanted.update(f"project://spec/capabilities/{a}" for a in aliases)
        return [t.id for t in ticket_records if t.derived_from in wanted]

    result = regenerate(
        brief=parsed_brief,
        existing=existing,
        ticket_lookup=lookup,
        now=datetime.now(timezone.utc),
    )

    spec_dict: dict | None = None
    if result.spec is not None:
        spec_dict = result.spec.model_dump(mode="json", by_alias=True)

    gap_dicts = [
        {
            "kind": g.kind,
            "location": g.location,
            "description": g.description,
            "severity": g.severity,
            "suggested_question": g.suggested_question,
        }
        for g in result.gaps
    ]
    return {"spec": spec_dict, "gaps": gap_dicts}
```

> Note: `TicketStore.list_all()` may not exist with that exact name — check `jig/store/tickets.py` for the equivalent method (it's likely `list()` or iteration on `_collection`). Use whichever exists.

- [ ] **Step 3: Register the MCP tool**

```python
# jig/mcp_server.py — add (in the spec-tool cluster, right after spec_load_existing)
    if "spec_generate_from_brief" in agent_cfg.allowed_tools:

        @tool(
            "spec_generate_from_brief",
            "Run the full deterministic spec generation pipeline: parse "
            "the brief, load the existing spec (if any), merge with "
            "metadata preservation, validate. Returns "
            "{spec: dict | None, gaps: [...]}. If spec is None, gaps "
            "contains blocking format errors or removed-from-brief "
            "issues — call spec_report_gaps with them. If spec is set, "
            "you may add semantic gaps (contradictions, ambiguities) of "
            "your own and then call spec_publish or spec_report_gaps. "
            "Spec-generator only.",
            {},
        )
        async def spec_generate_from_brief(args):
            out = await init_mcp.handle_spec_generate_from_brief(
                project_path=project_path,
                tickets=tickets,
            )
            return {"content": [{"type": "text", "text": json.dumps(out)}]}

        all_tools.append(spec_generate_from_brief)
```

- [ ] **Step 4: Run handler tests**

`uv run pytest tests/test_init_mcp_spec_query.py -v -k spec_generate_from_brief`
Expected: 3 PASS.

- [ ] **Step 5: Run full suite**

`uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add jig/init_mcp.py jig/mcp_server.py tests/test_init_mcp_spec_query.py
git commit -m "feat(spec-mcp): spec_generate_from_brief — deterministic pipeline as one tool"
```

---

## Phase 6: Role updates

**What:** Update `po.yaml`, `spec-generator.yaml`, `sa.yaml` prompts and `allowed_tools` lists. PO learns the new brief format with `{#id}` anchors and behavior/AC blocks. Spec-generator learns the regen algorithm and `spec_load_existing`. SA switches to capability-aware tools.

**Why:** Without prompt updates, the agents won't produce or consume the new schema even though the runtime supports it.

**Verify:** Existing role-config tests still pass (catalog validation); manual review of the prompts is the meaningful check (test code can't validate prompt quality directly).

### 6.1 Rewrite PO prompt for the new brief format

**Files:**
- Modify: `jig/defaults/roles/po.yaml`

- [ ] **Step 1: Read current `po.yaml`** to know what's there.

`cat jig/defaults/roles/po.yaml`

- [ ] **Step 2: Replace the prompt with the new format-aware version**

Replace the entire `phase_prompt:` block. Keep `allowed_tools` and the rest unchanged.

```yaml
# jig/defaults/roles/po.yaml — phase_prompt block (full replacement)
phase_prompt: >
  You are the Product Owner (PO) agent for `jig init`.


  ## What you're doing


  You are talking with the operator to author a project brief at
  `docs/brief.md`. The brief is the source of truth for product
  shape AND for stable IDs that downstream agents reference. You and
  the spec-generator are the only agents that read the brief markdown
  directly; every other agent works off a structured projection.


  ## Brief format (six sections, in this order)


  Each capability heading and bullet item carries a `{#kebab-id}`
  anchor that defines its stable ID. References to existing IDs use
  `[kebab-id]` brackets (no `#`). When you write the brief, ALWAYS
  include the anchor — downstream tools depend on these IDs being
  present and unique.


  - **Intro paragraph** (no heading) — what the product is and who it's for.

  - `## Built` — capabilities already shipped. Use `### Title {#id}`
    headings; usually empty at init.

  - `## Planned (committed)` — near-term capabilities. `### Title {#id}`
    headings with elaboration blocks (see "Capability content" below).

  - `## Planned (not yet committed)` — bullets:
    `- {#id} title and short description`. No behaviors, no AC.

  - `## Backlog` — bullets: `- {#id} idea`. No behaviors, no AC.

  - `## Archived` — capabilities once Built, now retired.
    `### Title {#id}` headings with brief reason.

  - `## Non-goals` — bullets: `- {#id} text — rationale`. The em-dash
    separates text from rationale.


  ## Capability content (in Built / Planned (committed) / Archived)


  Each `### Title {#id}` capability includes prose summary plus
  optional labelled blocks:


  ```

  ### Due dates {#due-dates}


  Users can give todos due dates and see when they're overdue.


  **User story:**

  As a busy person, I want due dates so I never miss a deadline.


  **Behaviors:**

  - {#set-due-date} Set a due date on any todo

  - {#overdue-indicator} Visual marker for overdue todos


  **Acceptance criteria:**

  - [set-due-date] A date can be attached to any todo

  - [set-due-date] Stored dates persist across reload

  - [overdue-indicator] Past-due todos display with a distinct visual marker


  **Excluded:**

  - Recurring dates

  - Reminders


  **Open questions:**

  - Time component, or date only?

  ```


  Notes on the format:

  - `{#id}` DEFINES an ID; `[id]` REFERENCES one.

  - Each AC bullet starts with `[behavior-id]` to attach to a specific
    behavior. Capability-level AC (no `[id]` reference) are valid when
    the capability has no behaviors.

  - **Acceptance criteria are mandatory** for capabilities under Built /
    Planned (committed) / Archived. Every behavior needs at least one AC,
    OR the capability needs at least one capability-level AC if it has no
    behaviors. Ask the operator: "What would you check to know this works?"
    right after they describe a behavior.


  ## Renaming a capability ID


  If the operator wants to rename a capability ID (e.g. `due-dates` →
  `deadlines`), update the anchor and add the old id as an alias:
  `### Deadlines {#deadlines aliases:due-dates}`. The structured spec's
  URI continuity depends on this — without the alias, tickets and
  decision records pointing at the old id break.


  ## Your tools (these are the only ones you have)


  - `brief_list_sections()` → list section names currently in the brief.

  - `brief_get_section(name)` → read one section ("_intro" for intro;
    H2 names without "## " for the rest).

  - `brief_set_section(name, content)` → write/replace one section
    atomically. Pass markdown content; the heading is implied by `name`.

  - `ask_question(ticket_id="brief", questions=[...])` → ask the
    operator. Pass `questions` as a list, one entry per question.

  - `po_finish_brief(summary)` → when the brief is ready, hand off to
    the spec-generator. After this returns, your run ends.


  Do NOT call `commit_progress`, `update_ticket`, `Bash`, `Glob`,
  `Grep`, `Edit`, `Write`. The brief lives in the MCP store; the
  structured spec lives in the MCP store; nothing needs to be committed.
  Resolving the ticket and handing off both happen automatically inside
  `po_finish_brief`.


  ## Out of scope for you


  Language, framework, deploy target, library choices, architectural
  deliberation. If the operator asks, tell them SA handles architecture
  after the brief is accepted, and steer back to product questions.


  ## What to do, by situation


  - **Brief is empty and no prior Q&A**: ask the operator one short
    pitch question first ("What is this and who is it for?"). One topic
    per turn until you have enough for the intro.

  - **You see prior Answers on the ticket**: read them, then either
    write the relevant sections or ask follow-ups if anything is still
    unclear. When elaborating a behavior, ALWAYS ask for at least one
    acceptance criterion right after the operator describes what the
    behavior does.

  - **Brief looks complete**: re-read each section once for sanity,
    then call `po_finish_brief(summary="…")`.
```

- [ ] **Step 3: Run catalog validation**

`uv run python -c "from jig.persistence import load_role; r = load_role(__import__('pathlib').Path('.'), 'po'); print(r.role)"`
Expected: prints `po` (no parse error).

- [ ] **Step 4: Run full suite**

`uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/defaults/roles/po.yaml
git commit -m "docs(po): rewrite prompt for {#id} anchored brief format"
```

### 6.2 Rewrite spec-generator prompt — thin orchestration around the pipeline tool

**Files:**
- Modify: `jig/defaults/roles/spec-generator.yaml`

The prompt is now thin because the brief parsing + regen merge are deterministic Python (run via `spec_generate_from_brief`). The agent's job is:

1. Call `spec_generate_from_brief()`.
2. If the tool returns `spec=None` and blocking gaps → forward to `spec_report_gaps`.
3. If the tool returns a `spec` dict → optionally Read the brief and look for SEMANTIC issues (contradictions between sections, ambiguous prose) the parser couldn't catch; if found, augment the gaps and call `spec_report_gaps`. Otherwise serialize the spec dict to YAML and call `spec_publish`.

- [ ] **Step 1: Replace the prompt and update `allowed_tools`**

```yaml
# jig/defaults/roles/spec-generator.yaml — full file
role: spec-generator
phase_prompt: >
  You are the spec-generator agent for `jig init`. You are one-shot
  and non-conversational.


  ## What you're doing


  Run the brief at `docs/brief.md` through the deterministic
  generation pipeline, then either publish or report gaps. Most of
  your work is mechanical and lives in the
  `spec_generate_from_brief` tool — your role is orchestration plus a
  semantic gap pass that the parser can't do mechanically.


  Don't ask the operator anything — you have no question tool. Report
  problems as gaps and exit.


  ## Algorithm


  1. Call `spec_generate_from_brief()`. The response is
     ``{spec: dict | None, gaps: [...]}``.

  2. If `spec is None`: the parser or merger found blocking issues.
     Pass `gaps` straight to `spec_report_gaps(gaps)` and exit.

  3. If `spec` is set: optionally Read `docs/brief.md` to look
     for SEMANTIC issues the parser cannot catch:

     - A non-goal text contradicting a planned/built capability
       title or summary (`severity: advisory`).

     - Capability prose that's ambiguous in a way that will bite SA
       (e.g., "real-time-ish" without saying live-update vs polling)
       (`severity: advisory`).

     - Anything obviously contradictory between two capabilities
       (`severity: blocking` if the contradiction is unresolvable).

     If you find any, append them to the existing `gaps` list (which
     may already contain advisory entries). Each gap dict needs `kind`,
     `location`, `description`, `severity`, optional
     `suggested_question`.

  4. If any gap has `severity: blocking`: call `spec_report_gaps(gaps)`
     and exit.

  5. Otherwise: serialize `spec` to YAML and call
     `spec_publish(yaml_content, advisory_notes)` with `advisory_notes`
     drawn from the advisory gaps.


  ## Your tools (these are the only ones you have)


  - `Read` (built-in) → read `docs/brief.md` for the semantic
    pass (step 3). The path is exactly that — no need to search.

  - `spec_generate_from_brief()` → run the full pipeline, returns
    `{spec, gaps}`.

  - `spec_publish(yaml_content, advisory_notes)` → happy path; ends
    your run.

  - `spec_report_gaps(gaps)` → failure path; ends your run.


  Do NOT call `commit_progress`, `update_ticket`, `Bash`, `Glob`,
  `Grep`, `Edit`, `Write`. Do NOT try to parse the brief yourself —
  use `spec_generate_from_brief`.


  ## Gap shape (for the semantic pass)


  ```yaml

  kind: contradiction | ambiguity | under_specified

  location: <e.g. "Planned (committed) > Due dates">

  description: <human-readable>

  severity: blocking | advisory

  suggested_question: <optional — what PO could ask the operator>

  ```


  See ``docs/reference/02a-project-spec-format.md`` for the full
  brief format and structured schema.
allowed_tools:
  - Read
  - spec_generate_from_brief
  - spec_publish
  - spec_report_gaps
allowed_mcps: []
strict_tools: true
default_context:
  - "ticket://description"
```

- [ ] **Step 2: Verify catalog still loads**

`uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add jig/defaults/roles/spec-generator.yaml
git commit -m "docs(spec-generator): full prompt rewrite — schema + regen algorithm"
```

### 6.3 Update SA prompt and allowed_tools

**Files:**
- Modify: `jig/defaults/roles/sa.yaml`

- [ ] **Step 1: Replace the prompt's "Process" / "What to do" sections to use capability-aware tools, and update `allowed_tools`**

```yaml
# jig/defaults/roles/sa.yaml — replacements

# In phase_prompt, the relevant blocks become:

#  ## Your tools (these are the only ones you have — don't reach for others)
#
#
#  - `spec_list_capabilities(state=?)` → list of {id, title, state};
#    optional state filter ('backlog', 'planned', 'in_progress',
#    'built', 'archived'). Lightweight overview.
#
#  - `spec_get_capability(id)` → full Capability with behaviors and AC.
#
#  - `spec_get_behavior(capability_id, behavior_id)` → one Behavior.
#
#  - `spec_list_non_goals()` / `spec_get_non_goal(id)` → non-goals.
#
#  - `spec_resolve_uri(uri)` → resolve any project://spec/... URI to
#    structured data.
#
#  - `arch_list_fields()` / `arch_get_field(name)` /
#    `arch_set_field(path, value)` → read/write architecture.yaml.
#    Use dotted paths for nested keys.
#
#  - `arch_list_templates()` → enumerate scaffold templates with metadata.
#
#  - `ask_question(ticket_id="architecture", questions=[...])` → scoping
#    questions; project-shape only (see below).
#
#  - `sa_propose_scaffold(template_name, rationale, config)` → propose
#    a template. Ends your run after operator accepts.

# And in "What to do, by situation":
#
#  - **First spawn**:
#    1. `spec_list_capabilities()` to see what's there.
#    2. `spec_get_capability(id)` for capabilities that look architecturally
#       interesting (anything with behaviors that imply storage, network,
#       UI shape, performance constraints).
#    3. `spec_list_non_goals()` if architecture choices might bump into
#       any of them.
#    4. Ask 0–2 scoping questions only if necessary.
#    5. Set `rationale` (multi-line prose) and any other architecture
#       fields you want to record.
#    6. `arch_list_templates()` to see what scaffolds are installed.
#    7. `sa_propose_scaffold(template_name, rationale, config)` with
#       the best fit.

allowed_tools:
  - Read
  - ask_question
  - spec_list_capabilities
  - spec_get_capability
  - spec_get_behavior
  - spec_list_non_goals
  - spec_get_non_goal
  - spec_resolve_uri
  - spec_get_field        # kept as fallback
  - spec_list_fields      # kept as fallback
  - arch_get_field
  - arch_set_field
  - arch_list_fields
  - arch_list_templates
  - sa_propose_scaffold
```

> Note: replace those two sections in `sa.yaml`'s existing `phase_prompt` (the "Your tools" and "What to do" blocks). Other sections (intro, question style, scope) stay unchanged.

- [ ] **Step 2: Verify catalog still loads + tests pass**

`uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add jig/defaults/roles/sa.yaml
git commit -m "docs(sa): switch to capability-aware spec tools"
```

---

## Phase 7: BRIEF_APPROVAL workflow

**What:** New `ResumeState.BRIEF_APPROVAL` between PO and spec-gen. CLI renders the brief and prompts; operator picks Y/r/n. Records `brief_approved` SystemEvent.

**Why:** Operator review gate before spec-gen runs.

**Verify:** New tests in `tests/test_init_workflow_brief_approval.py` pass; existing E2E tests update to include the approval step in their `click.prompt` answer iter.

### 7.1 Add the resume state and detection

**Files:**
- Modify: `jig/init_workflow.py`
- Modify: `tests/test_init_workflow_resume.py`

- [ ] **Step 1: Write a test for the new state**

```python
# tests/test_init_workflow_resume.py
async def test_resume_brief_approval_after_handoff_before_spec(wired):
    """After PO posts a Handoff, BRIEF_APPROVAL fires until the operator
    posts a brief_approved SystemEvent."""
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.BRIEF_APPROVAL
    )


async def test_resume_spec_generation_after_brief_approved(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief", author="cli",
            event_type="brief_approved", content="approved",
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.SPEC_GENERATION
    )


async def test_resume_brief_approval_again_after_re_handoff(wired):
    """Operator picked [r]esume PO; PO posted a new Handoff; we should
    fire BRIEF_APPROVAL again, not skip to SPEC_GENERATION."""
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    # First round
    await wired["threads"].post(Handoff(ticket_id="brief", author="po", phase="spec-generator", summary=""))
    await wired["threads"].post(SystemEvent(
        ticket_id="brief", author="cli",
        event_type="brief_approved", content="approved",
    ))
    # Second round (operator picked r the first time, PO re-finished)
    await wired["threads"].post(Handoff(ticket_id="brief", author="po", phase="spec-generator", summary=""))
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.BRIEF_APPROVAL
    )
```

- [ ] **Step 2: Update `ResumeState` enum and `classify_resume`**

```python
# jig/init_workflow.py — modify ResumeState
class ResumeState(str, Enum):
    PO_CONVERSATION = "po_conversation"
    NEEDS_ANSWER_BRIEF = "needs_answer_brief"
    BRIEF_APPROVAL = "brief_approval"     # NEW
    SPEC_GENERATION = "spec_generation"
    GAP_PROMPT = "gap_prompt"
    BRANCH_PROMPT = "branch_prompt"
    SA_CONVERSATION = "sa_conversation"
    NEEDS_ANSWER_ARCH = "needs_answer_arch"
    SA_CONFIRM_PROMPT = "sa_confirm_prompt"
    DIRECT_TEMPLATE_PICK = "direct_template_pick"
    ALREADY_DONE = "already_done"
    BROKEN = "broken"
```

In `classify_resume`, after the "no handoff yet" branch and before the "spec_generated" branch:

```python
# jig/init_workflow.py — inside classify_resume, modify the brief-section
# (after gap-detection logic, before the spec-generation transition)
# Find: `if has_handoff and not has_spec_gen_event:`
# (The code currently jumps straight to SPEC_GENERATION here.)

    # NEW: insert brief approval gate
    last_brief_approved_idx = -1
    for i, e in enumerate(brief_entries):
        if isinstance(e, SystemEvent) and e.event_type == "brief_approved":
            last_brief_approved_idx = i
    needs_approval = last_handoff_idx > last_brief_approved_idx

    if has_handoff and needs_approval and not has_spec_gen_event:
        return ResumeState.BRIEF_APPROVAL
    if has_handoff and not has_spec_gen_event:
        return ResumeState.SPEC_GENERATION
```

> Read `classify_resume` in full first to understand how `last_handoff_idx` etc. are already computed; the insertion point is just after the gap-prompt block.

- [ ] **Step 3: Run resume tests**

`uv run pytest tests/test_init_workflow_resume.py -v`
Expected: all PASS (including 3 new ones).

- [ ] **Step 4: Commit**

```bash
git add jig/init_workflow.py tests/test_init_workflow_resume.py
git commit -m "feat(init): BRIEF_APPROVAL resume state after PO handoff"
```

### 7.2 Implement the prompt + dispatch

**Files:**
- Modify: `jig/init_workflow.py`
- Create: `tests/test_init_workflow_brief_approval.py`

- [ ] **Step 1: Write a test for the prompt rendering**

```python
# tests/test_init_workflow_brief_approval.py
import asyncio
from pathlib import Path

import click
import pytest

from jig.init_workflow import (
    BriefApprovalChoice, prompt_brief_approval, render_brief_for_approval,
)


def test_render_brief_for_approval_includes_section_headers(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.md").write_text(
        "# x\n\nintro\n\n## Built\n\n(empty)\n\n## Non-goals\n\n- {#ng} no\n"
    )
    out = render_brief_for_approval(tmp_path)
    assert "# x" in out
    assert "Built" in out
    assert "Non-goals" in out


@pytest.mark.asyncio
async def test_prompt_brief_approval_yes(monkeypatch, tmp_path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.md").write_text("# x\n")
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "Y")
    monkeypatch.setattr(click, "echo", lambda *a, **kw: None)
    decision = await prompt_brief_approval(tmp_path)
    assert decision == BriefApprovalChoice.YES


@pytest.mark.asyncio
async def test_prompt_brief_approval_resume(monkeypatch, tmp_path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.md").write_text("# x\n")
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "r")
    monkeypatch.setattr(click, "echo", lambda *a, **kw: None)
    decision = await prompt_brief_approval(tmp_path)
    assert decision == BriefApprovalChoice.RESUME


@pytest.mark.asyncio
async def test_prompt_brief_approval_no(monkeypatch, tmp_path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.md").write_text("# x\n")
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "n")
    monkeypatch.setattr(click, "echo", lambda *a, **kw: None)
    decision = await prompt_brief_approval(tmp_path)
    assert decision == BriefApprovalChoice.NO
```

- [ ] **Step 2: Implement `BriefApprovalChoice`, `render_brief_for_approval`, `prompt_brief_approval`**

```python
# jig/init_workflow.py — add (next to ConfirmChoice / BranchChoice classes)
class BriefApprovalChoice(str, Enum):
    YES = "yes"
    RESUME = "resume"
    NO = "no"

    @classmethod
    def parse(cls, reply: str) -> "BriefApprovalChoice":
        r = reply.strip().lower()
        if r in ("", "y"):
            return cls.YES
        if r == "r":
            return cls.RESUME
        if r == "n":
            return cls.NO
        return cls.YES


def render_brief_for_approval(project_path: Path) -> str:
    """Read the brief markdown for approval display. Surrounds it with
    visual separators so the operator can scan where the brief starts
    and ends."""
    brief_path = project_path / ".jig" / "spec" / "project.md"
    text = brief_path.read_text() if brief_path.is_file() else "(brief is missing)"
    return (
        "─── Brief preview ──────────────────────────────────────\n"
        f"{text.rstrip()}\n"
        "────────────────────────────────────────────────────────\n"
    )


async def prompt_brief_approval(project_path: Path) -> BriefApprovalChoice:
    click.echo(render_brief_for_approval(project_path))
    click.echo(
        "Approve brief?\n"
        "  [Y] Hand off to spec-generator   (default)\n"
        "  [r] Resume PO — more changes needed\n"
        "  [n] Cancel — exit, state saved\n"
    )
    reply = click.prompt("Choice", default="Y", show_default=False)
    return BriefApprovalChoice.parse(reply)
```

- [ ] **Step 3: Wire `BRIEF_APPROVAL` into `run_init`'s loop**

```python
# jig/init_workflow.py — inside the run_init while loop, between
# NEEDS_ANSWER_BRIEF handler and SPEC_GENERATION handler:

        if rs == ResumeState.BRIEF_APPROVAL:
            decision = await prompt_brief_approval(target)
            if decision == BriefApprovalChoice.YES:
                await threads.post(
                    SystemEvent(
                        ticket_id="brief", author="cli",
                        event_type="brief_approved",
                        content="operator approved brief",
                    )
                )
                continue
            if decision == BriefApprovalChoice.RESUME:
                # Reactivate brief so PO respawn runs
                brief = await tickets.get("brief")
                if brief is not None and brief.status == TicketStatus.RESOLVED:
                    await tickets.update("brief", status=TicketStatus.IN_PROGRESS)
                continue
            # NO
            click.echo("Brief not approved. State saved.")
            return
```

- [ ] **Step 4: Run brief-approval tests**

`uv run pytest tests/test_init_workflow_brief_approval.py -v`
Expected: all PASS.

- [ ] **Step 5: Update existing E2E tests to include the brief approval prompt**

`grep -n "click.prompt" tests/test_init_workflow_e2e.py`
For each test that uses `iter([...])` of click.prompt answers, add `"Y"` for the brief approval at the right position. The order is now: brief approval → branch → confirm. So `iter(["Y", "Y", "Y"])` instead of `iter(["Y", "Y"])` for the SA happy path.

- [ ] **Step 6: Run E2E tests**

`uv run pytest tests/test_init_workflow_e2e.py -v`
Expected: all PASS.

- [ ] **Step 7: Run full suite**

`uv run pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add jig/init_workflow.py tests/test_init_workflow_brief_approval.py tests/test_init_workflow_e2e.py
git commit -m "feat(init): BRIEF_APPROVAL prompt + handler"
```

---

## Phase 8: Documentation

**What:** Reference doc that covers brief format and structured schema as a tight format reference. Original `docs/reference/02-project-spec.md` keeps the conceptual material; new doc serves as the format spec.

**Why:** PO/spec-gen prompts and human readers both need a focused doc to point at.

**Verify:** Doc renders cleanly; cross-links from prompts work.

### 8.1 Write the format reference doc

**Files:**
- Create: `docs/reference/02a-project-spec-format.md`

- [ ] **Step 1: Write the doc**

```markdown
# 02a — Project Spec Format Reference

Format spec for the project brief (`docs/brief.md`) and the
structured projection (`.jig/spec/project.structured.yaml`). For the
conceptual material on what a project spec is and why, see
[02 — Project Spec](./02-project-spec.md).

## Brief format

The brief is human-authored markdown. PO writes it. Spec-generator
reads it but never modifies it.

### Anchor syntax

- `{#kebab-id}` **defines** an ID at this location (heading or bullet).
- `[kebab-id]` **references** an existing ID elsewhere.
- `{#kebab-id aliases:old1,old2}` declares prior IDs that should still
  resolve via URIs (used during ID renames).

IDs are kebab-case (lowercase letters, digits, hyphens; no leading hyphen).

### Sections (in order)

```markdown
# <project name>

<intro paragraph — becomes spec.summary>

## Built

### <Capability title> {#capability-id}

<prose summary>

**User story:** (optional)
As a <persona>, I want <capability> so that <benefit>.

**Behaviors:**
- {#behavior-id} <one-line description>

**Acceptance criteria:**
- [behavior-id] <testable condition>
- [behavior-id] <another condition>

**Excluded:** (optional)
- <capability-level non-goal>

**Open questions:** (optional)
- <question for follow-up>

## Planned (committed)

(same elaborated structure as Built)

## Planned (not yet committed)

- {#capability-id} <one-line title and description>

## Backlog

- {#capability-id} <one-line idea>

## Archived

(same elaborated structure as Built; for capabilities once Built, now retired)

## Non-goals

- {#non-goal-id} <text> — <rationale>
```

### Section → state mapping

| Brief section | Structured `state` |
|---|---|
| `## Built` | `built` |
| `## Planned (committed)` | `planned` (or `in_progress` if a ticket is open against the ID) |
| `## Planned (not yet committed)` | `planned` |
| `## Backlog` | `backlog` |
| `## Archived` | `archived` |
| `## Non-goals` | not capabilities — go to `non_goals` |

### Format rules (each is a blocking gap)

- Every `### <title>` heading has trailing `{#slug}`.
- Every bullet under Backlog / Planned-not-committed / Non-goals has leading `{#slug}`.
- Every behavior bullet has leading `{#behavior-id}`.
- Every AC bullet has leading `[behavior-id]`, OR is capability-level (no reference, only allowed when capability has no behaviors).
- AC `[behavior-id]` references resolve to a behavior in the same capability.
- IDs unique across the brief; aliases unique and don't collide with any id.
- Capability with `state ∈ {planned, in_progress, built}` has at least one AC somewhere (capability-level or via behaviors).
- Backlog / Planned-not-committed bullets have no behaviors blocks, no AC blocks.

## Structured schema

YAML at `.jig/spec/project.structured.yaml`. Generated by spec-gen
from the brief. Pydantic-validated.

```yaml
name: <string>                     # H1 of the brief
summary: <string>                  # intro paragraph
capabilities:                      # list of Capability
  - id: <kebab-slug>
    title: <string>
    state: backlog | planned | in_progress | built | archived
    summary: <string>
    user_story:                    # optional
      as: <string>
      want: <string>
      benefit: <string>
    behaviors:                     # may be []
      - id: <kebab-slug>
        description: <string>
        examples: [<string>, ...]  # optional
        acceptance_criteria: [<string>, ...]  # ≥1 if behaviors exist
    acceptance_criteria: [<string>, ...]      # capability-level
    excluded: [<string>, ...]
    open_questions: [<string>, ...]
    tickets: [<ticket-id>, ...]    # populated by harness from ticket store
    aliases: [<kebab-slug>, ...]   # from brief anchor
    created_at: <iso8601>
    last_updated: <iso8601>
    state_changed_at: <iso8601>
non_goals:
  - id: <kebab-slug>
    text: <string>
    rationale: <string>            # may be ""
    aliases: [<kebab-slug>, ...]
generated_at: <iso8601>
spec_version: 1
```

## URI scheme

| URI | Resolves to |
|---|---|
| `project://spec` | The whole `StructuredSpec` |
| `project://spec/name` | The `name` field |
| `project://spec/summary` | The `summary` field |
| `project://spec/capabilities` | Full list of capabilities |
| `project://spec/capabilities/<id>` | One Capability by ID (or alias) |
| `project://spec/capabilities/<id>#<behavior-id>` | One Behavior |
| `project://spec/non-goals` | Full list of non-goals |
| `project://spec/non-goals/<id>` | One NonGoal by ID (or alias) |
| `project://spec/state/<state-name>` | All capabilities with that state |

URIs are strict — relative URIs are rejected; the full `project://spec/`
prefix is required. Unknown IDs raise loudly. Empty fragment (`#`) is
treated as no fragment.

Tickets reference the capability they derive from via the
`Ticket.derived_from` field, e.g. `derived_from: "project://spec/capabilities/due-dates"`.

## Regeneration

When PO edits the brief, spec-gen re-runs. It preserves the
operator-owned metadata (id, aliases, created_at, state_changed_at,
tickets) for matched capabilities and overwrites brief-derived content.

Capabilities are matched by exact ID first, then by any of the brief's
aliases against the existing capability's id-or-aliases (the rename
case).

Capabilities that exist in `project.structured.yaml` but not in the
brief surface as blocking gaps. The operator resolves by editing the
brief alone — adding the capability back to a section, moving it to
`## Archived`, or declaring an alias.

Spec-gen never modifies the brief.
```

- [ ] **Step 2: Add a back-pointer in 02-project-spec.md**

```markdown
# docs/reference/02-project-spec.md — add at the top, after the H1
> **For the format spec** (brief markdown structure, structured YAML
> schema, URI scheme, regeneration rules), see
> [02a — Project Spec Format Reference](./02a-project-spec-format.md).
> This doc covers the conceptual material — what a project spec is
> and why.
```

- [ ] **Step 3: Commit**

```bash
git add docs/reference/02a-project-spec-format.md docs/reference/02-project-spec.md
git commit -m "docs(reference): split format reference from conceptual project-spec doc"
```

---

## Phase 9: End-to-end smoke

**What:** Run `jig init dogfood --force` against the dogfood project. Verify the new flow works front to back: PO writes a brief with anchors, brief approval prompts, spec-gen produces a schema-valid structured.yaml, SA queries via capability-aware tools, scaffold applies.

**Why:** Final integration validation. Catches anything the unit tests missed.

**Verify:** A complete `jig init dogfood --force` run produces `docs/brief.md` with `{#id}` anchors, `.jig/spec/project.structured.yaml` that parses as `StructuredSpec`, and `.jig/spec/architecture.yaml`.

### 9.1 Smoke run

**Files:**
- None to edit; manual run.

- [ ] **Step 1: Wipe the dogfood project and run init**

```bash
cd /Users/brent/Projects/personal/jig_dogfood
jig init dogfood --force
```

Answer PO's questions in a way that exercises:
- At least one Built capability (or accept empty)
- At least two Planned (committed) capabilities, each with 2 behaviors and 2 AC per behavior
- At least one Backlog item
- At least one Non-goal
- One ID rename mid-conversation if you want to exercise aliases

- [ ] **Step 2: Verify the brief**

```bash
cat dogfood/docs/brief.md
```

Look for: `### <title> {#id}` heading anchors; `**Behaviors:**` blocks with `- {#id}` bullets; `**Acceptance criteria:**` blocks with `- [behavior-id]` bullets.

- [ ] **Step 3: Verify the structured spec validates**

```bash
uv run python -c "
import yaml
from jig.spec_schema import StructuredSpec
from pathlib import Path
data = yaml.safe_load(Path('dogfood/.jig/spec/project.structured.yaml').read_text())
s = StructuredSpec.model_validate(data)
print(f'spec valid: {s.name}, {len(s.capabilities)} capabilities, {len(s.non_goals)} non-goals')
for c in s.capabilities:
    print(f'  - {c.id} ({c.state.value}) — {len(c.behaviors)} behaviors')
"
```

Expected: `spec valid: ...`, no Pydantic ValidationError.

- [ ] **Step 4: Verify URI resolution**

```bash
uv run python -c "
import yaml
from pathlib import Path
from jig.spec_schema import StructuredSpec
from jig.spec_uri import resolve_spec_uri
data = yaml.safe_load(Path('dogfood/.jig/spec/project.structured.yaml').read_text())
s = StructuredSpec.model_validate(data)
first_cap = s.capabilities[0]
uri = f'project://spec/capabilities/{first_cap.id}'
print(uri)
print(resolve_spec_uri(uri, s))
"
```

Expected: prints the URI and the resolved capability data.

- [ ] **Step 5: Verify architecture.yaml exists**

```bash
cat dogfood/.jig/spec/architecture.yaml
```

Expected: contains `template`, `template_applied_at`, etc. from the SA flow.

- [ ] **Step 6: If anything fails, the failure mode dictates the next move**

| Failure | Action |
|---|---|
| PO produces brief without anchors | PO prompt didn't take; revisit Phase 6.1 |
| Brief parser rejects PO's brief | Format rule too strict, or PO prompt allowed something it shouldn't; refine accordingly |
| Spec-gen schema-rejects | Spec-gen prompt produced invalid YAML; refine prompt or schema |
| BRIEF_APPROVAL never fires | classify_resume bug; check Phase 7.1 wiring |
| SA can't find capability tools | Phase 6.3 SA allowed_tools mismatch |

- [ ] **Step 7: Commit any prompt/code adjustments needed from the smoke run, then re-run**

Loop until a clean `jig init dogfood --force` produces all three artifacts and validation passes.

- [ ] **Step 8: Final commit**

```bash
git add jig/defaults/roles/*.yaml jig/init_workflow.py  # whatever changed
git commit -m "chore(spec-schema): smoke-test fixups from end-to-end run"
```

---

## Rollback

The work spans 9 phases of additive code (new modules: `spec_schema`, `brief_parser`, `spec_regeneration`, `spec_uri`) plus modifications to existing handlers, role configs, and workflow. Mid-stream revert:

1. Each phase is committed separately. `git revert <commit-hash>` for any single phase.
2. Phase 1.5 adds `Ticket.derived_from` — a new optional field with default `None`. No existing call site sets it; revert is risk-free.
3. Phase 6 prompt rewrites are textual; revert restores prior prompts and the older flow keeps working (the schema validation in Phase 1.4 would then start rejecting old-style spec YAMLs — keep that revert in mind).
4. The `dogfood/.jig/` artifacts are reset on every `--force` run; nothing persistent to worry about.

Hardest revert is Phase 1.4 (`handle_spec_publish` validation) once Phase 6.2 ships — the spec-gen prompt assumes the schema. Revert in reverse order: 9 → 8 → 7 → 6 → 5 → 4 → 3 → 2 → 1.

## Out of scope for this plan

- `jig spec migrate-v1` CLI for migrating prototype-era spec files. Per design.
- Hard-deletion of capabilities via the brief. Future CLI command.
- Tests-to-AC mapping; AC objects with their own IDs. Reserved by `[id]` vs `{#id}` syntax distinction.
- BDD-style structured AC.
- Capability dependencies / blocks-graph.
- Sub-element addressing within a capability beyond Behaviors.
- Diff/history tooling.
- Multiple project specs per repo.

## Change log

- 2026-04-27: Initial draft (brent)
