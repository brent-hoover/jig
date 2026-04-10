import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jig.bus import MessageBus
from jig.bus_monitor import BusMonitor
from jig.models import AgentInstance, AgentMessage, AgentStatus, Issue, MessageDirection
from jig.persistence import save_agent_instance, save_issue


class TestBusMonitor:
    @pytest.fixture
    def setup(self, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        save_agent_instance(tmp_jig_project, AgentInstance(
            id="dev-1", agent_type="dev", status=AgentStatus.DORMANT,
        ))
        bus = MessageBus(tmp_jig_project)
        return tmp_jig_project, bus

    async def test_detects_message_to_dormant_agent(self, setup):
        project_path, bus = setup
        callback = AsyncMock()
        monitor = BusMonitor(project_path, bus, "issue-1", on_wake=callback)
        monitor_task = asyncio.create_task(monitor.start())

        await asyncio.sleep(0.1)

        msg = AgentMessage(
            sender_id="test-1",
            recipient_id="dev-1",
            direction=MessageDirection.REQUEST,
            topic="question",
            content="How should I test this?",
        )
        await bus.publish("issue-1", msg)

        await asyncio.sleep(0.3)

        callback.assert_called_once()
        call_args = callback.call_args
        assert call_args[0][0] == "dev-1"  # agent_id
        assert call_args[0][1].content == "How should I test this?"  # message

        monitor.stop()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    async def test_ignores_message_to_active_agent(self, setup, tmp_jig_project: Path):
        project_path, bus = setup
        # Change agent to active
        save_agent_instance(tmp_jig_project, AgentInstance(
            id="dev-1", agent_type="dev", status=AgentStatus.ACTIVE,
        ))
        callback = AsyncMock()
        monitor = BusMonitor(project_path, bus, "issue-1", on_wake=callback)
        monitor_task = asyncio.create_task(monitor.start())
        await asyncio.sleep(0.1)

        msg = AgentMessage(
            sender_id="test-1",
            recipient_id="dev-1",
            direction=MessageDirection.REQUEST,
            topic="question",
            content="question",
        )
        await bus.publish("issue-1", msg)
        await asyncio.sleep(0.3)

        callback.assert_not_called()

        monitor.stop()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    async def test_ignores_broadcast(self, setup):
        project_path, bus = setup
        callback = AsyncMock()
        monitor = BusMonitor(project_path, bus, "issue-1", on_wake=callback)
        monitor_task = asyncio.create_task(monitor.start())
        await asyncio.sleep(0.1)

        msg = AgentMessage(
            sender_id="test-1",
            recipient_id="broadcast",
            direction=MessageDirection.REQUEST,
            topic="status",
            content="update",
        )
        await bus.publish("issue-1", msg)
        await asyncio.sleep(0.3)

        callback.assert_not_called()

        monitor.stop()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
