import asyncio

import pytest
from jig.store.bus import Message, MessageBus, MessageType


def test_message_type_enum_values():
    assert MessageType.TASK_ASSIGNMENT.value == "task_assignment"
    assert MessageType("question") == MessageType.QUESTION


def test_message_construction_with_attribute_names():
    msg = Message(
        sender="alice",
        to="bob",
        type=MessageType.QUESTION,
        payload={"q": "hi"},
        topic="JIG-1",
    )
    assert msg.sender == "alice"
    assert msg.to == "bob"
    assert msg.type == MessageType.QUESTION
    assert msg.topic == "JIG-1"
    assert msg.correlation_id is None
    assert isinstance(msg.timestamp, str)
    assert isinstance(msg.id, str)


def test_message_serializes_sender_as_from_alias():
    msg = Message(
        sender="alice",
        to="bob",
        type=MessageType.STATUS,
        payload={},
        topic="JIG-1",
    )
    dumped = msg.model_dump(mode="json", by_alias=True)
    assert dumped["from"] == "alice"
    assert "sender" not in dumped
    assert dumped["_id"] == msg.id


def test_message_roundtrip_through_dict():
    m1 = Message(
        sender="alice",
        to="bob",
        type=MessageType.ANSWER,
        payload={"k": 1},
        topic="JIG-1",
    )
    raw = m1.model_dump(mode="json", by_alias=True)
    m2 = Message.model_validate(raw)
    assert m2.sender == "alice"
    assert m2.to == "bob"
    assert m2.type == MessageType.ANSWER
    assert m2.id == m1.id


def test_message_can_be_built_from_dict_with_from_key():
    raw = {
        "_id": "x",
        "from": "alice",
        "to": "bob",
        "type": "status",
        "payload": {},
        "timestamp": "2026-04-10T00:00:00+00:00",
        "topic": "JIG-1",
    }
    msg = Message.model_validate(raw)
    assert msg.sender == "alice"
    assert msg.type == MessageType.STATUS


async def test_bus_publish_accepts_message_instance(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    msg = Message(
        sender="alice", to="bob", type=MessageType.STATUS,
        payload={}, topic="JIG-1",
    )
    msg_id = await bus.publish(msg)
    assert isinstance(msg_id, str)
    assert msg_id == msg.id


async def test_bus_publish_accepts_dict_and_validates(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    msg_id = await bus.publish({
        "from": "alice",
        "to": "bob",
        "type": "status",
        "payload": {},
        "topic": "JIG-1",
    })
    assert isinstance(msg_id, str)


async def test_bus_single_subscriber_receives_message(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    queue = await bus.subscribe("JIG-1")
    await bus.publish(Message(
        sender="alice", to="bob", type=MessageType.STATUS,
        payload={}, topic="JIG-1",
    ))
    received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received.sender == "alice"


async def test_bus_subscriber_does_not_receive_other_topic(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    queue = await bus.subscribe("JIG-1")
    await bus.publish(Message(
        sender="alice", to="bob", type=MessageType.STATUS,
        payload={}, topic="JIG-2",
    ))
    assert queue.empty()


async def test_bus_fans_out_to_multiple_subscribers(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    q1 = await bus.subscribe("JIG-1")
    q2 = await bus.subscribe("JIG-1")
    await bus.publish(Message(
        sender="a", to="b", type=MessageType.STATUS,
        payload={}, topic="JIG-1",
    ))
    m1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    m2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert m1.sender == "a" and m2.sender == "a"


async def test_bus_unsubscribe_stops_delivery_to_that_queue(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    q1 = await bus.subscribe("JIG-1")
    q2 = await bus.subscribe("JIG-1")
    await bus.unsubscribe("JIG-1", q1)
    await bus.publish(Message(
        sender="a", to="b", type=MessageType.STATUS,
        payload={}, topic="JIG-1",
    ))
    assert q1.empty()
    assert (await asyncio.wait_for(q2.get(), timeout=1.0)).sender == "a"


async def test_bus_unsubscribe_unknown_queue_is_noop(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    stray: asyncio.Queue[Message] = asyncio.Queue()
    # Should not raise
    await bus.unsubscribe("JIG-1", stray)


async def test_bus_get_history_oldest_first(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    for i in range(3):
        await bus.publish(Message(
            sender="a", to="b", type=MessageType.STATUS,
            payload={"i": i}, topic="JIG-1",
        ))
    history = await bus.get_history("JIG-1")
    assert [m.payload["i"] for m in history] == [0, 1, 2]


async def test_bus_get_history_respects_limit(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    for i in range(5):
        await bus.publish(Message(
            sender="a", to="b", type=MessageType.STATUS,
            payload={"i": i}, topic="JIG-1",
        ))
    history = await bus.get_history("JIG-1", limit=2)
    assert [m.payload["i"] for m in history] == [3, 4]


async def test_bus_websocket_listener_fires_on_publish(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    received: list[Message] = []

    async def listener(msg: Message) -> None:
        received.append(msg)

    await bus.add_websocket_listener(listener)
    await bus.publish(Message(
        sender="a", to="b", type=MessageType.STATUS,
        payload={}, topic="JIG-1",
    ))
    assert len(received) == 1 and received[0].sender == "a"


async def test_bus_raising_listener_does_not_break_delivery(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    queue = await bus.subscribe("JIG-1")

    async def broken(msg: Message) -> None:
        raise RuntimeError("boom")

    await bus.add_websocket_listener(broken)
    # Publish should not raise
    await bus.publish(Message(
        sender="a", to="b", type=MessageType.STATUS,
        payload={}, topic="JIG-1",
    ))
    received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received.sender == "a"


async def test_bus_load_does_not_replay_to_queues(tmp_path):
    path = tmp_path / "messages.jsonl"
    bus1 = MessageBus(path)
    await bus1.load()
    await bus1.publish(Message(
        sender="a", to="b", type=MessageType.STATUS,
        payload={}, topic="JIG-1",
    ))

    bus2 = MessageBus(path)
    await bus2.load()
    queue = await bus2.subscribe("JIG-1")
    # No new message has been published on bus2
    assert queue.empty()


async def test_bus_crash_recovery_via_get_history(tmp_path):
    path = tmp_path / "messages.jsonl"
    bus1 = MessageBus(path)
    await bus1.load()
    await bus1.publish(Message(
        sender="a", to="b", type=MessageType.STATUS,
        payload={"i": 1}, topic="JIG-1",
    ))
    await bus1.publish(Message(
        sender="a", to="b", type=MessageType.STATUS,
        payload={"i": 2}, topic="JIG-1",
    ))

    bus2 = MessageBus(path)
    await bus2.load()
    history = await bus2.get_history("JIG-1")
    assert [m.payload["i"] for m in history] == [1, 2]
