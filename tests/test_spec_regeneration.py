from datetime import datetime, timezone


from jig.brief_parser import (
    BriefBehavior, BriefCapability, ParsedBriefResult,
)
from jig.spec_regeneration import regenerate
from jig.spec_schema import CapabilityState


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
