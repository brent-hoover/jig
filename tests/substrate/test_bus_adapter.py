"""Substrate bones — TypedBus compat adapter (Epic 2, task 4).

The adapter wraps the existing ``MessageBus`` and accepts both the legacy
``Message``/``dict`` (old string topics) and the new ``TypedEvent``. Typed
events are down-converted to ``Message`` and published through the wrapped bus.
Every other ``MessageBus`` method is delegated unchanged, so the adapter is a
drop-in for the raw bus.
"""

from __future__ import annotations

from jig.store.bus import Message, MessageBus, MessageType
from jig.substrate.bus import TypedBus
from jig.substrate.events import TicketUpdated


async def _fresh_bus(tmp_path) -> MessageBus:
    bus = MessageBus(tmp_path / "bus.jsonl")
    await bus.load()
    return bus


async def test_publishes_typed_event_to_its_default_per_ticket_topic(tmp_path) -> None:
    bus = await _fresh_bus(tmp_path)
    tb = TypedBus(bus)
    queue = await bus.subscribe("tickets.jig-1")

    # No explicit topic -> routes to the per-ticket topic, not orchestrator.
    await tb.publish(TicketUpdated(ticket_id="jig-1", status="open"))

    msg = await queue.get()
    assert msg.topic == "tickets.jig-1"
    assert msg.type == MessageType.CONTEXT_UPDATE
    assert msg.payload == {
        "kind": "ticket_updated",
        "ticket_id": "jig-1",
        "status": "open",
    }


async def test_publishes_orchestrator_dispatch_copy_when_topic_explicit(
    tmp_path,
) -> None:
    bus = await _fresh_bus(tmp_path)
    tb = TypedBus(bus)
    queue = await bus.subscribe("orchestrator")

    await tb.publish(
        TicketUpdated(ticket_id="jig-1", status="open", topic="orchestrator")
    )

    msg = await queue.get()
    assert msg.topic == "orchestrator"
    assert msg.payload["kind"] == "ticket_updated"


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


async def test_delegates_other_bus_methods(tmp_path) -> None:
    """The adapter is a drop-in: methods beyond publish/subscribe delegate to
    the wrapped bus (the gap roborev flagged)."""
    bus = await _fresh_bus(tmp_path)
    tb = TypedBus(bus)

    # subscribe_agent returns a stable queue and receives published events
    agent_q = await tb.subscribe_agent("orchestrator", "agent-1")
    await tb.publish(
        TicketUpdated(ticket_id="jig-1", status="open", topic="orchestrator")
    )
    msg = await agent_q.get()
    assert msg.payload["ticket_id"] == "jig-1"

    # get_history and recent delegate to the wrapped collection
    history = await tb.get_history("orchestrator")
    assert any(m.payload.get("ticket_id") == "jig-1" for m in history)
    recent = await tb.recent(kind="ticket_updated")
    assert recent and recent[-1].payload["kind"] == "ticket_updated"
