"""Tests for jig.spec_schema — CapabilityState, UserStory, Behavior, NonGoal."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jig.spec_schema import CapabilityState, UserStory, Behavior, NonGoal


def test_capability_state_values():
    assert {s.value for s in CapabilityState} == {
        "backlog", "planned", "in_progress", "built", "archived",
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
