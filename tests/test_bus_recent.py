from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from jig.store import MessageBus, Message, MessageType


@pytest.fixture
async def bus(tmp_path: Path) -> MessageBus:
    b = MessageBus(tmp_path / "messages.jsonl")
    await b.load()
    return b


def _make_msg(topic: str, kind: str, ts: datetime) -> Message:
    return Message(
        sender="test",
        to="broadcast",
        type=MessageType.CONTEXT_UPDATE,
        topic=topic,
        payload={"kind": kind},
        timestamp=ts,
    )


@pytest.mark.asyncio
async def test_recent_returns_empty_for_no_messages(bus: MessageBus):
    assert await bus.recent() == []


@pytest.mark.asyncio
async def test_recent_returns_sorted_by_timestamp(bus: MessageBus):
    base = datetime(2026, 4, 28, tzinfo=timezone.utc)
    await bus.publish(_make_msg("a", "k1", base + timedelta(seconds=2)))
    await bus.publish(_make_msg("b", "k2", base + timedelta(seconds=1)))
    await bus.publish(_make_msg("c", "k3", base + timedelta(seconds=3)))
    out = await bus.recent()
    assert [m.payload["kind"] for m in out] == ["k2", "k1", "k3"]


@pytest.mark.asyncio
async def test_recent_caps_to_limit(bus: MessageBus):
    base = datetime(2026, 4, 28, tzinfo=timezone.utc)
    for i in range(20):
        await bus.publish(_make_msg("x", f"k{i}", base + timedelta(seconds=i)))
    out = await bus.recent(limit=5)
    assert len(out) == 5
    # Most recent 5: k15..k19
    assert [m.payload["kind"] for m in out] == [f"k{i}" for i in range(15, 20)]


@pytest.mark.asyncio
async def test_recent_filters_by_kind(bus: MessageBus):
    base = datetime(2026, 4, 28, tzinfo=timezone.utc)
    await bus.publish(_make_msg("x", "ticket_updated", base + timedelta(seconds=1)))
    await bus.publish(_make_msg("y", "comment_posted", base + timedelta(seconds=2)))
    await bus.publish(_make_msg("z", "ticket_updated", base + timedelta(seconds=3)))
    out = await bus.recent(kind="ticket_updated")
    assert len(out) == 2
    assert all(m.payload["kind"] == "ticket_updated" for m in out)
