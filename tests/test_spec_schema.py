"""Tests for jig.spec_schema — CapabilityState, UserStory, Behavior, NonGoal."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from jig.spec_schema import Capability, CapabilityState, UserStory, Behavior, NonGoal, StructuredSpec


def _now() -> datetime:
    return datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


def _ts() -> dict:
    """Common timestamp kwargs."""
    n = _now()
    return {"created_at": n, "last_updated": n, "state_changed_at": n}


def test_capability_state_values():
    assert {s.value for s in CapabilityState} == {
        "backlog", "planned_uncommitted", "planned", "in_progress", "built", "archived",
    }


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


def test_user_story_serializes_without_alias():
    """Without by_alias=True, the field name `as_` (not the alias `as`)
    is what shows up in the dump output. Both paths matter because
    different consumers may want either form."""
    s = UserStory(**{"as": "x", "want": "y", "benefit": "z"})
    assert s.model_dump() == {"as_": "x", "want": "y", "benefit": "z"}


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


def test_behavior_id_rejects_trailing_hyphen():
    with pytest.raises(ValidationError):
        Behavior(id="trailing-", description="x", acceptance_criteria=["y"])


def test_behavior_id_accepts_single_character():
    """Single-char slug is valid — bracket case in the regex."""
    b = Behavior(id="a", description="x", acceptance_criteria=["y"])
    assert b.id == "a"


def test_capability_by_id_or_alias_finds_by_id():
    spec = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(id="due-dates", title="t",
                       state=CapabilityState.BACKLOG, **_ts()),
        ],
        generated_at=_now(),
    )
    found = spec.capability_by_id_or_alias("due-dates")
    assert found is not None
    assert found.id == "due-dates"


def test_capability_by_id_or_alias_finds_by_alias():
    spec = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(id="deadlines", title="t",
                       state=CapabilityState.BACKLOG,
                       aliases=["due-dates"], **_ts()),
        ],
        generated_at=_now(),
    )
    found = spec.capability_by_id_or_alias("due-dates")
    assert found is not None
    assert found.id == "deadlines"


def test_capability_by_id_or_alias_returns_none_for_unknown():
    spec = StructuredSpec(name="x", summary="y", generated_at=_now())
    assert spec.capability_by_id_or_alias("nope") is None


def test_non_goal_by_id_or_alias_finds_by_alias():
    spec = StructuredSpec(
        name="x", summary="y",
        non_goals=[
            NonGoal(id="no-multi-user", text="multi", aliases=["no-collab"]),
        ],
        generated_at=_now(),
    )
    found = spec.non_goal_by_id_or_alias("no-collab")
    assert found is not None
    assert found.id == "no-multi-user"


def test_capability_in_progress_requires_ac():
    """The validator must enforce all three AC-required states, not
    just `planned`. Regression coverage."""
    with pytest.raises(ValidationError):
        Capability(
            id="x", title="t",
            state=CapabilityState.IN_PROGRESS,
            **_ts(),
        )


def test_capability_built_requires_ac():
    with pytest.raises(ValidationError):
        Capability(
            id="x", title="t",
            state=CapabilityState.BUILT,
            **_ts(),
        )
