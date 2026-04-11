from pathlib import Path

import pytest

from jig import mcp_server
from jig.models import AgentTypeConfig
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore


@pytest.mark.asyncio
async def test_agent_mcp_server_registers_expected_tools(
    tmp_path: Path, monkeypatch
) -> None:
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    comments = CommentStore(tmp_path / "comments.jsonl")
    await comments.load()
    memory = MemoryStore(tmp_path)  # directory, not file
    await memory.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    cfg = AgentTypeConfig(role="dev", phase_prompt="", can_message=["user"])

    captured: dict = {}

    def fake_create_server(name, tools, **_kwargs):
        captured["name"] = name
        captured["tools"] = tools
        return {"type": "sdk", "name": name, "instance": None}

    monkeypatch.setattr(mcp_server, "create_sdk_mcp_server", fake_create_server)

    result = mcp_server.create_agent_mcp_server(
        tickets=tickets,
        comments=comments,
        memory=memory,
        bus=bus,
        agent_role="dev",
        agent_cfg=cfg,
        worktree_path=tmp_path / "worktree",
    )

    assert result is not None
    tool_names = {t.name for t in captured["tools"]}
    assert tool_names == {
        "create_ticket",
        "read_ticket",
        "update_ticket",
        "comment_on_ticket",
        "list_tickets",
        "read_comments",
        "commit_progress",
        "record_learning",
        "request_context",
    }
