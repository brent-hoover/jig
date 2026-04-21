from jig.models import PhaseConfig, RoleConfig
from jig.project import Project
from jig.prompt_builder import SpawnReason, build_initial_prompt
from jig.skill_loader import Skill
from jig.thread import Note
from jig.ticket import Ticket, TicketStatus, WorkType


def _project() -> Project:
    return Project(
        id="p", name="p", path="/tmp",
        language="python", package_manager="uv",
        test_command="uv run pytest",
    )


def _cfg() -> RoleConfig:
    return RoleConfig(role="dev", phase_prompt="You are dev.", response_prompt="You answer.")


def _ticket() -> Ticket:
    return Ticket(
        work_type=WorkType.REFACTOR, title="implement X", created_by="orchestrator",
        description="do the thing", status=TicketStatus.OPEN,
    )


def test_injection_order() -> None:
    parent = Ticket(
        work_type=WorkType.FEATURE, title="parent", created_by="user",
        description="overall goal",
    )
    uv_skill = Skill(
        name="uv", source_filename="uv.md", applies_to={},
        content="# uv\n\nalways uv run",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=parent,
        entries=[],
        memories=["use pytest-asyncio"],
        project=_project(),
        skills=[uv_skill],
        environment_md="## Env\nnever touch legacy/\n",
    )
    assert prompt.index("You are dev.") < prompt.index("## Project Context")
    assert prompt.index("## Project Context") < prompt.index("# uv")
    assert prompt.index("# uv") < prompt.index("## Env")
    assert prompt.index("## Env") < prompt.index("use pytest-asyncio")
    assert prompt.index("use pytest-asyncio") < prompt.index("implement X")
    assert "overall goal" in prompt


def test_qa_responder_uses_response_prompt() -> None:
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.QA_RESPONDER,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "You answer." in prompt
    assert "You are dev." not in prompt


def test_qa_responder_falls_back_to_phase_prompt_with_preamble() -> None:
    cfg = RoleConfig(role="dev", phase_prompt="You are dev.")
    prompt = build_initial_prompt(
        role_cfg=cfg,
        spawn_reason=SpawnReason.QA_RESPONDER,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "answering a question" in prompt.lower()
    assert "You are dev." in prompt


def test_parent_comments_included() -> None:
    parent = Ticket(work_type=WorkType.FEATURE, title="p", created_by="u", description="")
    parent_entries = [
        Note(ticket_id="parent-id", author="spec-writer", text="use redis"),
        Note(ticket_id="parent-id", author="spec-writer", text="index by id"),
    ]
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=parent,
        entries=parent_entries,
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "use redis" in prompt
    assert "index by id" in prompt


def test_phase_section_interpolates_task_template() -> None:
    phase = PhaseConfig(
        name="implement",
        role="dev",
        task_template="Implement code that passes the tests for: {ticket_title}",
        acceptance_criteria="All tests pass",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        phase=phase,
    )
    assert "## Phase: implement" in prompt
    assert "Implement code that passes the tests for: implement X" in prompt
    assert "All tests pass" in prompt


def test_phase_section_accepts_legacy_issue_title_placeholder() -> None:
    phase = PhaseConfig(
        name="spec",
        role="spec",
        task_template="Draft spec for: {issue_title}",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        phase=phase,
    )
    assert "Draft spec for: implement X" in prompt


def test_phase_section_unknown_placeholder_left_intact() -> None:
    """Unknown placeholders degrade to visible text rather than crashing."""
    phase = PhaseConfig(
        name="x", role="dev", task_template="work on {nonexistent}"
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        phase=phase,
    )
    assert "work on {nonexistent}" in prompt


def test_phase_section_absent_when_phase_none() -> None:
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "## Phase:" not in prompt
    assert "### Acceptance criteria" not in prompt
