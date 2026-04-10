import asyncio
from pathlib import Path

import pytest

from jig.bus import MessageBus
from jig.models import Issue, Message, MessageType
from jig.persistence import save_issue


class TestMessageBus:
    @pytest.fixture
    def bus(self, tmp_jig_project: Path) -> MessageBus:
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        return MessageBus(tmp_jig_project)

    async def test_subscribe_and_publish(self, bus: MessageBus):
        queue = await bus.subscribe("issue-1", "orchestrator")
        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.STATUS,
        )
        await bus.publish("issue-1", msg)
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.sender == "dev-agent"

    async def test_broadcast(self, bus: MessageBus):
        q1 = await bus.subscribe("issue-1", "agent-a")
        q2 = await bus.subscribe("issue-1", "agent-b")
        msg = Message(
            sender="orchestrator",
            recipient="broadcast",
            type=MessageType.STATUS,
        )
        await bus.publish("issue-1", msg)
        r1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert r1.id == r2.id

    async def test_targeted_delivery(self, bus: MessageBus):
        q_a = await bus.subscribe("issue-1", "agent-a")
        q_b = await bus.subscribe("issue-1", "agent-b")
        msg = Message(
            sender="orchestrator",
            recipient="agent-a",
            type=MessageType.TASK_ASSIGNMENT,
        )
        await bus.publish("issue-1", msg)
        received = await asyncio.wait_for(q_a.get(), timeout=1.0)
        assert received.sender == "orchestrator"
        assert q_b.empty()

    async def test_persists_to_jsonl(self, bus: MessageBus, tmp_jig_project: Path):
        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.TASK_COMPLETION,
            payload={"result": "done"},
        )
        await bus.publish("issue-1", msg)
        jsonl_path = tmp_jig_project / ".jig" / "issues" / "issue-1" / "messages.jsonl"
        assert jsonl_path.is_file()
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 1

    async def test_replay_from_jsonl(self, tmp_jig_project: Path):
        """A new bus instance can replay persisted messages."""
        save_issue(tmp_jig_project, Issue(id="issue-2", title="Replay test"))
        bus1 = MessageBus(tmp_jig_project)
        for i in range(3):
            msg = Message(
                sender=f"agent-{i}",
                recipient="orchestrator",
                type=MessageType.STATUS,
                payload={"seq": i},
            )
            await bus1.publish("issue-2", msg)

        bus2 = MessageBus(tmp_jig_project)
        messages = bus2.replay("issue-2")
        assert len(messages) == 3
        assert messages[0].payload["seq"] == 0
        assert messages[2].payload["seq"] == 2

    async def test_tap_receives_all_messages(self, bus: MessageBus, tmp_jig_project: Path):
        """A tap receives both targeted and broadcast messages."""
        tap_queue = await bus.tap("issue-1")

        # Send a targeted message
        msg1 = Message(
            sender="dev-agent",
            recipient="test-agent",
            type=MessageType.STATUS,
        )
        await bus.publish("issue-1", msg1)

        # Send a broadcast
        msg2 = Message(
            sender="dev-agent",
            recipient="broadcast",
            type=MessageType.STATUS,
        )
        await bus.publish("issue-1", msg2)

        received = []
        while not tap_queue.empty():
            received.append(await tap_queue.get())

        assert len(received) == 2
