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
