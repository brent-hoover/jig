import pytest

from jig.brief_parser import (
    BriefCapability,
    BriefBehavior,
    BriefNonGoal,
    parse_anchor,
    AnchorParseError,
    parse_reference,
    ReferenceParseError,
    split_into_sections,
    parse_elaborated_section,
    parse_bullet_section,
    parse_non_goals_section,
    BriefParseError,
)


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


# --- anchor parser ---


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


# --- reference parser ---


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


# --- section splitter ---

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


# --- elaborated section parser ---

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
    body = (
        "### Deadlines {#deadlines aliases:due-dates}\n\nProse.\n\n"
        "**Acceptance criteria:**\n- Deadlines are saved\n"
    )
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


# --- bullet section parser ---


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


# --- non-goals section parser ---


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
