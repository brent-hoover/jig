from datetime import datetime, timezone


from jig.brief_parser import (
    BriefBehavior,
    BriefCapability,
    ParsedBriefResult,
)
from jig.spec_regeneration import regenerate
from jig.spec_schema import Capability, CapabilityState, StructuredSpec


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


# §3.2 — match-and-preserve


def test_regenerate_preserves_created_at_for_matched_id():
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x",
        summary="y",
        capabilities=[
            Capability(
                id="due-dates",
                title="Due dates",
                state=CapabilityState.PLANNED,
                acceptance_criteria=["a date can be set"],
                created_at=earlier,
                last_updated=earlier,
                state_changed_at=earlier,
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
        brief=brief,
        existing=existing,
        ticket_lookup=lambda cid, aliases: [],
        now=_ts(),
    )
    assert result.spec is not None
    cap = result.spec.capabilities[0]
    assert cap.created_at == earlier  # preserved
    assert cap.last_updated == _ts()  # bumped
    assert cap.title == "Due dates (revised)"  # overwritten


def test_regenerate_bumps_state_changed_at_when_state_changes():
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x",
        summary="y",
        capabilities=[
            Capability(
                id="due-dates",
                title="Due dates",
                state=CapabilityState.PLANNED,
                acceptance_criteria=["a date can be set"],
                created_at=earlier,
                last_updated=earlier,
                state_changed_at=earlier,
            ),
        ],
        generated_at=earlier,
    )
    brief = _empty_brief(
        capabilities=[
            BriefCapability(
                id="due-dates",
                title="Due dates",
                section="archived",  # was planned, now archived
                capability_acceptance_criteria=["a date can be set"],
            ),
        ],
    )
    result = regenerate(
        brief=brief,
        existing=existing,
        ticket_lookup=lambda cid, aliases: [],
        now=_ts(),
    )
    cap = result.spec.capabilities[0]
    assert cap.state == CapabilityState.ARCHIVED
    assert cap.state_changed_at == _ts()


def test_regenerate_matches_via_brief_alias():
    """Brief's anchor declares aliases; existing has the alias as id —
    rename detected, existing entry merged into brief's new id."""
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x",
        summary="y",
        capabilities=[
            Capability(
                id="due-dates",
                title="Due dates",
                state=CapabilityState.PLANNED,
                acceptance_criteria=["x"],
                created_at=earlier,
                last_updated=earlier,
                state_changed_at=earlier,
            ),
        ],
        generated_at=earlier,
    )
    brief = _empty_brief(
        capabilities=[
            BriefCapability(
                id="deadlines",
                aliases=["due-dates"],
                title="Deadlines",
                section="planned_committed",
                capability_acceptance_criteria=["x"],
            ),
        ],
    )
    result = regenerate(
        brief=brief,
        existing=existing,
        ticket_lookup=lambda cid, aliases: [],
        now=_ts(),
    )
    cap = result.spec.capabilities[0]
    assert cap.id == "deadlines"  # renamed per brief
    assert cap.aliases == ["due-dates"]  # alias kept (operator declared it)
    assert cap.created_at == earlier  # original timestamp preserved


# §3.3 — removed-from-brief gap


def test_regenerate_surfaces_removed_capability_as_gap():
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x",
        summary="y",
        capabilities=[
            Capability(
                id="dropped-feature",
                title="Dropped feature",
                state=CapabilityState.PLANNED,
                acceptance_criteria=["x"],
                created_at=earlier,
                last_updated=earlier,
                state_changed_at=earlier,
            ),
        ],
        generated_at=earlier,
    )
    brief = _empty_brief()  # no capabilities at all
    result = regenerate(
        brief=brief,
        existing=existing,
        ticket_lookup=lambda cid, aliases: [],
        now=_ts(),
    )
    assert result.spec is None
    assert len(result.gaps) == 1
    assert result.gaps[0].kind == "removed_from_brief"
    assert "dropped-feature" in result.gaps[0].location


# §3.4 — ticket lookup


def test_regenerate_populates_capability_tickets_from_lookup():
    """`ticket_lookup` is called per capability; spec-gen wires it
    against the ticket store at integration time."""
    brief = _empty_brief(
        capabilities=[
            BriefCapability(
                id="due-dates",
                title="Due dates",
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
        brief=brief,
        existing=None,
        ticket_lookup=lookup,
        now=_ts(),
    )
    assert result.spec.capabilities[0].tickets == ["ticket-42", "ticket-43"]
