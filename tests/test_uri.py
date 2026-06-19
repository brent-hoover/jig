"""Tests for the multi-authority project URI parser + spec resolver.

Spec authority is the only resolver wired up for v2 bones; arch/design/plan/
store parse cleanly but raise UnimplementedAuthorityError at resolve time.
"""

from datetime import datetime, timezone

import pytest

from jig.spec_schema import (
    Behavior,
    Capability,
    CapabilityState,
    NonGoal,
    StructuredSpec,
)
from jig.uri import (
    ProjectUri,
    ProjectUriError,
    UnimplementedAuthorityError,
    parse_project_uri,
    resolve_project_uri,
    resolve_spec_uri,
)
from jig.uri.errors import UnknownAuthorityError


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_root_spec():
    p = parse_project_uri("project://spec")
    assert p == ProjectUri(
        authority="spec", path=(), revision=None, fragment=None, fragment_style="none"
    )


def test_parse_capability_by_id():
    p = parse_project_uri("project://spec/capabilities/due-dates")
    assert p.authority == "spec"
    assert p.path == ("capabilities", "due-dates")
    assert p.revision is None
    assert p.fragment is None


def test_parse_capability_with_anchor_fragment():
    p = parse_project_uri("project://spec/capabilities/due-dates#sort-by-due-date")
    assert p.path == ("capabilities", "due-dates")
    assert p.fragment == "sort-by-due-date"
    assert p.fragment_style == "anchor"


def test_parse_state_collection():
    p = parse_project_uri("project://spec/state/planned")
    assert p.path == ("state", "planned")


def test_parse_non_goal_by_id():
    p = parse_project_uri("project://spec/non-goals/no-multi-user")
    assert p.path == ("non-goals", "no-multi-user")


def test_parse_rejects_relative():
    with pytest.raises(ProjectUriError, match="prefix"):
        parse_project_uri("capabilities/due-dates")


def test_parse_rejects_other_scheme():
    with pytest.raises(ProjectUriError, match="prefix"):
        parse_project_uri("ticket://abc")


def test_parse_treats_empty_fragment_as_no_fragment():
    p = parse_project_uri("project://spec/capabilities/x#")
    assert p.fragment is None
    assert p.fragment_style == "none"


def test_parse_arch_authority():
    p = parse_project_uri("project://arch/architecture")
    assert p.authority == "arch"
    assert p.path == ("architecture",)


def test_parse_arch_with_revision_and_path_fragment():
    p = parse_project_uri(
        "project://arch/modules/catalog-ingest/contracts@revision:7#owns/products"
    )
    assert p.authority == "arch"
    assert p.path == ("modules", "catalog-ingest", "contracts")
    assert p.revision == 7
    assert p.fragment == "owns/products"
    assert p.fragment_style == "path"


def test_parse_revision_without_fragment():
    p = parse_project_uri("project://arch/architecture@revision:5")
    assert p.revision == 5
    assert p.fragment is None


def test_parse_rejects_unknown_authority():
    with pytest.raises(UnknownAuthorityError, match="unknown authority"):
        parse_project_uri("project://garbage/something")


def test_parse_rejects_malformed_revision():
    with pytest.raises(ProjectUriError, match="revision"):
        parse_project_uri("project://arch/architecture@revision:abc")


def test_parse_rejects_uppercase_segment():
    with pytest.raises(ProjectUriError, match="path segment"):
        parse_project_uri("project://spec/Capabilities/x")


def test_parse_design_plan_store_authorities():
    assert parse_project_uri("project://design/wireframes/post").authority == "design"
    assert parse_project_uri("project://plan/build/epics/x").authority == "plan"
    assert parse_project_uri("project://store/tickets/t-001").authority == "store"


# ---------------------------------------------------------------------------
# Spec resolver tests
# ---------------------------------------------------------------------------


def _ts():
    return datetime(2026, 4, 27, tzinfo=timezone.utc)


def _spec() -> StructuredSpec:
    return StructuredSpec(
        name="todoapp",
        summary="A simple todo manager.",
        capabilities=[
            Capability(
                id="due-dates",
                title="Due dates",
                state=CapabilityState.PLANNED,
                behaviors=[
                    Behavior(
                        id="set-due-date",
                        description="x",
                        acceptance_criteria=["a"],
                    ),
                ],
                created_at=_ts(),
                last_updated=_ts(),
                state_changed_at=_ts(),
            ),
            Capability(
                id="priorities",
                title="Priorities",
                state=CapabilityState.BACKLOG,
                created_at=_ts(),
                last_updated=_ts(),
                state_changed_at=_ts(),
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
    with pytest.raises(ProjectUriError, match="capability"):
        resolve_spec_uri("project://spec/capabilities/nope", _spec())


def test_resolve_capability_behavior_fragment():
    out = resolve_spec_uri(
        "project://spec/capabilities/due-dates#set-due-date", _spec()
    )
    assert out["kind"] == "behavior"
    assert out["data"]["id"] == "set-due-date"


def test_resolve_unknown_behavior_fragment_raises():
    with pytest.raises(ProjectUriError, match="behavior"):
        resolve_spec_uri("project://spec/capabilities/due-dates#nope", _spec())


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
    with pytest.raises(ProjectUriError, match="state"):
        resolve_spec_uri("project://spec/state/bogus", _spec())


def test_resolve_rejects_revision_pin_for_v1_spec():
    with pytest.raises(ProjectUriError, match="revision"):
        resolve_spec_uri("project://spec@revision:3", _spec())


def test_resolve_rejects_non_spec_authority():
    parsed = parse_project_uri("project://arch/architecture")
    with pytest.raises(ProjectUriError, match="non-spec"):
        resolve_spec_uri(parsed, _spec())


# ---------------------------------------------------------------------------
# Dispatcher: unimplemented authorities
# ---------------------------------------------------------------------------


def test_dispatcher_arch_raises_unimplemented(tmp_path):
    with pytest.raises(UnimplementedAuthorityError, match="arch"):
        resolve_project_uri("project://arch/architecture", tmp_path)


def test_dispatcher_design_raises_unimplemented(tmp_path):
    with pytest.raises(UnimplementedAuthorityError, match="design"):
        resolve_project_uri("project://design/wireframes/post", tmp_path)


def test_dispatcher_plan_raises_unimplemented(tmp_path):
    with pytest.raises(UnimplementedAuthorityError, match="plan"):
        resolve_project_uri("project://plan/build/epics/x", tmp_path)


def test_dispatcher_store_raises_unimplemented(tmp_path):
    with pytest.raises(UnimplementedAuthorityError, match="store"):
        resolve_project_uri("project://store/tickets/t-001", tmp_path)
