import asyncio


from jig.store import Message, MessageBus, MessageType


async def test_bus_publish_and_subscribe_roundtrip(tmp_path):
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    queue = await bus.subscribe("JIG-1")
    await bus.publish(Message(
        sender="orchestrator",
        to="dev-agent",
        type=MessageType.TASK_ASSIGNMENT,
        payload={"task_id": "T1"},
        topic="JIG-1",
    ))
    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert msg.sender == "orchestrator"
    assert msg.to == "dev-agent"
    assert msg.type == MessageType.TASK_ASSIGNMENT


async def test_bus_crash_recovery_via_history(tmp_path):
    path = tmp_path / "messages.jsonl"
    bus1 = MessageBus(path)
    await bus1.load()
    await bus1.publish(Message(
        sender="a", to="b", type=MessageType.STATUS,
        payload={}, topic="JIG-1",
    ))

    bus2 = MessageBus(path)
    await bus2.load()
    history = await bus2.get_history("JIG-1")
    assert len(history) == 1
    assert history[0].sender == "a"


async def test_shared_bus_cross_agent_delivery(tmp_path):
    """Two subscribers on the same bus instance both see a published message.

    Regression test for the broken pattern where each agent process
    constructed its own ``MessageBus`` and thus never saw publishes from
    any other agent. The orchestrator now threads a single bus through
    ``run_agent`` so live fan-out works.
    """
    bus = MessageBus(tmp_path / ".jig" / "store" / "messages.jsonl")
    await bus.load()
    q1 = await bus.subscribe("JIG-1")
    q2 = await bus.subscribe("JIG-1")
    await bus.publish(Message(
        sender="a", to="b", type=MessageType.STATUS,
        payload={}, topic="JIG-1",
    ))
    msg1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    msg2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert msg1.sender == "a"
    assert msg2.sender == "a"
