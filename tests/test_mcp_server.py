from pathlib import Path

import pytest

from jig import mcp_server
from jig.models import RoleConfig
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


@pytest.mark.asyncio
async def test_agent_mcp_server_registers_expected_tools(
    tmp_path: Path, monkeypatch
) -> None:
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    comments = CommentStore(tmp_path / "comments.jsonl")
    await comments.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    memory = MemoryStore(tmp_path)  # directory, not file
    await memory.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    cfg = RoleConfig(role="dev", phase_prompt="")

    captured: dict = {}

    def fake_create_server(name, tools, **_kwargs):
        captured["name"] = name
        captured["tools"] = tools
        return {"type": "sdk", "name": name, "instance": None}

    monkeypatch.setattr(mcp_server, "create_sdk_mcp_server", fake_create_server)

    result = mcp_server.create_agent_mcp_server(
        tickets=tickets,
        comments=comments,
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
    assert tool_names == {
        "create_ticket",
        "read_ticket",
        "update_ticket",
        "comment_on_ticket",
        "ask_question",
        "thread_ask",
        "thread_answer",
        "thread_resolve_question",
        "list_tickets",
        "read_comments",
        "commit_progress",
        "record_learning",
        "request_context",
    }
