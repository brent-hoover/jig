from pathlib import Path

import pytest

from jig import mcp_server
from jig.models import RoleConfig
from jig.store import MessageBus
from jig.store.checkpoints import CheckpointStore
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


_BASE_TOOLS = {
    "create_ticket",
    "read_ticket",
    "update_ticket",
    "comment_on_ticket",
    "ask_question",
    "thread_ask",
    "thread_answer",
    "thread_resolve_question",
    "thread_object",
    "thread_resolve_objection",
    "thread_accept_resolution",
    "thread_waive",
    "thread_waive_check",
    "thread_decide",
    "thread_note",
    "thread_escalate",
    "thread_uncertain",
    "thread_handoff",
    "thread_accept_handoff",
    "thread_reject_handoff",
    "list_tickets",
    "read_comments",
    "commit_progress",
    "record_learning",
    "request_context",
}

_CHECKPOINT_TOOLS = {
    "checkpoint_milestone",
    "checkpoint_decision",
    "checkpoint_deferred",
    "checkpoint_promote_deferred",
}


async def _make_common_stores(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    memory = MemoryStore(tmp_path)
    await memory.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    return tickets, threads, memory, bus


def _patch_create_server(monkeypatch, captured: dict):
    def fake_create_server(name, tools, **_kwargs):
        captured["name"] = name
        captured["tools"] = tools
        return {"type": "sdk", "name": name, "instance": None}

    monkeypatch.setattr(mcp_server, "create_sdk_mcp_server", fake_create_server)


@pytest.mark.asyncio
async def test_agent_mcp_server_registers_expected_tools(
    tmp_path: Path, monkeypatch
) -> None:
    tickets, threads, memory, bus = await _make_common_stores(tmp_path)
    cfg = RoleConfig(role="dev", phase_prompt="")

    captured: dict = {}
    _patch_create_server(monkeypatch, captured)

    result = mcp_server.create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="dev",
        agent_cfg=cfg,
        worktree_path=tmp_path / "worktree",
        project_path=tmp_path,
    )

    assert result is not None
    tool_names = {t.name for t in captured["tools"]}
    # Without a CheckpointStore the checkpoint_* tools are not registered;
    # agent.py always provides one, but older call sites (tests, operator
    # spawns) still work without.
    assert tool_names == _BASE_TOOLS


@pytest.mark.asyncio
async def test_checkpoint_tools_registered_when_store_provided(
    tmp_path: Path, monkeypatch
) -> None:
    tickets, threads, memory, bus = await _make_common_stores(tmp_path)
    checkpoints = CheckpointStore(tmp_path / "checkpoints.jsonl")
    await checkpoints.load()
    cfg = RoleConfig(role="dev", phase_prompt="")

    captured: dict = {}
    _patch_create_server(monkeypatch, captured)

    mcp_server.create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="dev",
        agent_cfg=cfg,
        worktree_path=tmp_path / "worktree",
        project_path=tmp_path,
        checkpoints=checkpoints,
        phase_name="implement",
    )
    tool_names = {t.name for t in captured["tools"]}
    assert tool_names == _BASE_TOOLS | _CHECKPOINT_TOOLS


@pytest.mark.asyncio
async def test_phase_allowlists_enforced_through_mcp_tools(
    tmp_path: Path, monkeypatch
) -> None:
    """Phase 5 Task K: the MCP factory routes the phase's
    ``questions_to`` / ``escalation_targets`` frozensets into the
    ``thread_ask`` / ``thread_escalate`` tool closures so end-to-end
    tool calls refuse disallowed targets."""
    tickets, threads, memory, bus = await _make_common_stores(tmp_path)
    ticket_id = await tickets.create(
        __import__("jig.ticket", fromlist=["Ticket", "WorkType"]).Ticket(
            work_type=__import__("jig.ticket", fromlist=["WorkType"]).WorkType.FEATURE,
            title="t",
            created_by="orchestrator",
        )
    )
    cfg = RoleConfig(role="dev", phase_prompt="")

    captured: dict = {}
    _patch_create_server(monkeypatch, captured)

    mcp_server.create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="dev",
        agent_cfg=cfg,
        worktree_path=tmp_path / "worktree",
        project_path=tmp_path,
        phase_questions_to=frozenset({"reviewer"}),
        phase_escalation_targets=frozenset({"sa"}),
    )
    tools_by_name = {t.name: t for t in captured["tools"]}

    from jig.thread_mcp import ThreadError

    # thread_ask refuses a target outside the phase list.
    with pytest.raises(ThreadError, match="questions_to"):
        await tools_by_name["thread_ask"].handler(
            {"ticket_id": ticket_id, "target": "po", "question": "?"}
        )
    # any_human still works despite the narrow list.
    await tools_by_name["thread_ask"].handler(
        {"ticket_id": ticket_id, "target": "any_human", "question": "?"}
    )

    # thread_escalate refuses a target outside the phase list.
    with pytest.raises(ThreadError, match="escalation_targets"):
        await tools_by_name["thread_escalate"].handler(
            {
                "ticket_id": ticket_id,
                "reason": "x",
                "details": "y",
                "target": "po",
            }
        )
    # Default "human" target still passes.
    await tools_by_name["thread_escalate"].handler(
        {"ticket_id": ticket_id, "reason": "x", "details": "y"}
    )


@pytest.mark.asyncio
async def test_ask_question_dedupes_repeated_entries_in_list(
    tmp_path: Path, monkeypatch
) -> None:
    """Models sometimes repeat the same prompt inside ``questions``; the
    tool dedupes (whitespace-trimmed) so the operator doesn't see the
    same question twice."""
    from jig.thread import Question
    from jig.ticket import Ticket, WorkType

    tickets, threads, memory, bus = await _make_common_stores(tmp_path)
    ticket_id = await tickets.create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
        )
    )
    cfg = RoleConfig(role="po", phase_prompt="")

    captured: dict = {}
    _patch_create_server(monkeypatch, captured)

    mcp_server.create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="po",
        agent_cfg=cfg,
        worktree_path=tmp_path / "worktree",
        project_path=tmp_path,
    )
    tools_by_name = {t.name: t for t in captured["tools"]}

    await tools_by_name["ask_question"].handler(
        {
            "ticket_id": ticket_id,
            "questions": ["What is it?", "What is it?", " What is it? "],
        }
    )

    qs = [
        e for e in await threads.for_ticket(ticket_id) if isinstance(e, Question)
    ]
    assert len(qs) == 1, f"expected 1 Question after dedupe, got {len(qs)}"
    assert qs[0].question == "What is it?"


@pytest.mark.asyncio
async def test_ask_question_keeps_distinct_questions(
    tmp_path: Path, monkeypatch
) -> None:
    """Distinct questions in one call must still all be posted."""
    from jig.thread import Question
    from jig.ticket import Ticket, WorkType

    tickets, threads, memory, bus = await _make_common_stores(tmp_path)
    ticket_id = await tickets.create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
        )
    )
    cfg = RoleConfig(role="po", phase_prompt="")

    captured: dict = {}
    _patch_create_server(monkeypatch, captured)

    mcp_server.create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="po",
        agent_cfg=cfg,
        worktree_path=tmp_path / "worktree",
        project_path=tmp_path,
    )
    tools_by_name = {t.name: t for t in captured["tools"]}

    await tools_by_name["ask_question"].handler(
        {
            "ticket_id": ticket_id,
            "questions": ["who?", "what?", "why?"],
        }
    )

    qs = [
        e for e in await threads.for_ticket(ticket_id) if isinstance(e, Question)
    ]
    assert [q.question for q in qs] == ["who?", "what?", "why?"]
