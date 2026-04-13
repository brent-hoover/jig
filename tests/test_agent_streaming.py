"""Unit tests for run_agent under streaming input mode."""
import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from jig.models import AgentTypeConfig
from jig.project import Project
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketType


async def _wait_for_subscription(bus, topic: str, timeout: float = 2.0) -> None:
    """Poll the bus's internal subscriber list until the topic has at least one
    queue registered.  Touching a private attribute is intentional here — this
    is test-only synchronisation that needs to observe internal state before
    publishing, to avoid a race where the publish precedes the subscribe."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if bus._subscribers.get(topic):
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"no subscriber for {topic} within {timeout}s")


async def _make_context(tmp_path: Path) -> AgentSpawnContext:
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    comments = CommentStore(tmp_path / "comments.jsonl")
    await comments.load()
    memory = MemoryStore(tmp_path)
    await memory.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    t = Ticket(
        type=TicketType.TASK,
        title="t",
        created_by="o",
        description="do it",
    )
    tid = await tickets.create(t)
    loaded = await tickets.get(tid)
    assert loaded is not None
    return AgentSpawnContext(
        role="dev",
        role_cfg=AgentTypeConfig(role="dev", phase_prompt="be dev"),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=loaded,
        parent=None,
        worktree_path=tmp_path / "worktree",
        project=Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
        tickets=tickets,
        comments=comments,
        memory=memory,
        bus=bus,
    )


def _fake_result_message():
    class _M:
        result = "done"
        num_turns = 1
        duration_ms = 1
        total_cost_usd = 0.0
    return _M()


@pytest.mark.asyncio
async def test_run_agent_builds_initial_prompt(tmp_path: Path) -> None:
    ctx = await _make_context(tmp_path)

    captured_prompt_iter = None

    async def fake_query(prompt, options, **kwargs):
        nonlocal captured_prompt_iter
        captured_prompt_iter = prompt
        async for _turn in prompt:
            yield _fake_result_message()
            return

    from jig import agent as agent_module
    with patch.object(agent_module, "query", fake_query):
        await agent_module.run_agent(ctx)

    assert captured_prompt_iter is not None


@pytest.mark.asyncio
async def test_run_agent_yields_incoming_bus_events(tmp_path: Path) -> None:
    ctx = await _make_context(tmp_path)
    seen_turns: list[str] = []

    async def fake_query(prompt, options, **kwargs):
        async for turn in prompt:
            seen_turns.append(turn if isinstance(turn, str) else str(turn))
            if len(seen_turns) >= 2:
                yield _fake_result_message()
                return

    async def publish_delayed():
        await _wait_for_subscription(ctx.bus, f"tickets.{ctx.ticket.id}")
        from jig.store import Message, MessageType
        await ctx.bus.publish(Message(
            sender="qa",
            to="dev",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "comment_posted",
                "author": "qa",
                "ticket_id": ctx.ticket.id,
                "content": "did you handle edge X?",
            },
            topic=f"tickets.{ctx.ticket.id}",
        ))
        await ctx.bus.publish(Message(
            sender="dev",
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "ticket_updated",
                "ticket_id": ctx.ticket.id,
                "status": "resolved",
            },
            topic=f"tickets.{ctx.ticket.id}",
        ))

    from jig import agent as agent_module
    with patch.object(agent_module, "query", fake_query):
        await asyncio.gather(
            agent_module.run_agent(ctx),
            publish_delayed(),
        )

    assert len(seen_turns) == 2
    assert "did you handle edge X" in seen_turns[1]
