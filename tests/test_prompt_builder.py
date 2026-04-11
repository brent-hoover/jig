from jig.models import AgentTypeConfig
from jig.project import Project
from jig.prompt_builder import SpawnReason, build_initial_prompt
from jig.skill_loader import Skill
from jig.ticket import Comment, Ticket, TicketStatus, TicketType


def _project() -> Project:
    return Project(
        id="p", name="p", path="/tmp",
        language="python", package_manager="uv",
        test_command="uv run pytest",
    )


def _cfg() -> AgentTypeConfig:
    return AgentTypeConfig(role="dev", phase_prompt="You are dev.", response_prompt="You answer.")


def _ticket() -> Ticket:
    return Ticket(
        type=TicketType.TASK, title="implement X", created_by="orchestrator",
        description="do the thing", status=TicketStatus.OPEN,
    )


def test_injection_order() -> None:
    parent = Ticket(
        type=TicketType.FEATURE, title="parent", created_by="user",
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
        comments=[],
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
        comments=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "You answer." in prompt
    assert "You are dev." not in prompt


def test_qa_responder_falls_back_to_phase_prompt_with_preamble() -> None:
    cfg = AgentTypeConfig(role="dev", phase_prompt="You are dev.")
    prompt = build_initial_prompt(
        role_cfg=cfg,
        spawn_reason=SpawnReason.QA_RESPONDER,
        ticket=_ticket(),
        parent=None,
        comments=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "answering a question" in prompt.lower()
    assert "You are dev." in prompt


def test_parent_comments_included() -> None:
    parent = Ticket(type=TicketType.FEATURE, title="p", created_by="u", description="")
    parent_comments = [
        Comment(ticket_id="parent-id", author="spec-writer", content="use redis"),
        Comment(ticket_id="parent-id", author="spec-writer", content="index by id"),
    ]
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=parent,
        comments=parent_comments,
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "use redis" in prompt
    assert "index by id" in prompt
