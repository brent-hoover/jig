"""Substrate bones — TypedBus compat adapter (Epic 2, task 4).

The adapter wraps the existing ``MessageBus`` and accepts both the legacy
``Message``/``dict`` (old string topics) and the new ``TypedEvent``. Typed
events are down-converted to ``Message`` and published through the wrapped bus,
so existing subscribers see no difference.
"""

from __future__ import annotations

from jig.store.bus import Message, MessageBus, MessageType
from jig.substrate.bus import TypedBus
from jig.substrate.events import TicketCompleted


async def _fresh_bus(tmp_path) -> MessageBus:
    bus = MessageBus(tmp_path / "bus.jsonl")
    await bus.load()
    return bus


async def test_publishes_typed_event_as_message(tmp_path) -> None:
    bus = await _fresh_bus(tmp_path)
    tb = TypedBus(bus)
    queue = await bus.subscribe("orchestrator")

    await tb.publish(TicketCompleted(ticket_id="jig-1"))

    msg = await queue.get()
    assert msg.topic == "orchestrator"
    assert msg.payload["kind"] == "ticket_completed"
    assert msg.payload["ticket_id"] == "jig-1"


async def test_still_accepts_legacy_message(tmp_path) -> None:
    bus = await _fresh_bus(tmp_path)
    tb = TypedBus(bus)
    queue = await bus.subscribe("custom-topic")

    await tb.publish(
        Message(
            sender="agent-a",
            to="orchestrator",
            type=MessageType.STATUS,
            payload={"kind": "ticket_created"},
            topic="custom-topic",
        )
    )

    msg = await queue.get()
    assert msg.topic == "custom-topic"
    assert msg.payload["kind"] == "ticket_created"


async def test_still_accepts_legacy_dict(tmp_path) -> None:
    bus = await _fresh_bus(tmp_path)
    tb = TypedBus(bus)
    queue = await bus.subscribe("custom-topic")

    await tb.publish(
        {
            "from": "agent-a",
            "to": "orchestrator",
            "type": "status",
            "payload": {"kind": "ticket_created"},
            "topic": "custom-topic",
        }
    )

    msg = await queue.get()
    assert msg.payload["kind"] == "ticket_created"
