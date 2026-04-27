from datetime import datetime, timezone

import pytest

from jig.spec_schema import (
    Behavior, Capability, CapabilityState, NonGoal, StructuredSpec,
)
from jig.spec_uri import parse_spec_uri, resolve_spec_uri, SpecUri, SpecUriError


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


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------


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
