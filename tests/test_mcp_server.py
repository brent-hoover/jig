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
