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
