import pytest
from jig.store.bus import Message, MessageType


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
