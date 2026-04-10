"""Bus monitor — watches for messages to dormant agents and triggers wake-up."""

import asyncio
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from jig.bus import MessageBus
from jig.models import AgentMessage, AgentStatus
from jig.persistence import load_agent_instance


class BusMonitor:
    def __init__(
        self,
        project_path: Path,
        bus: MessageBus,
        issue_id: str,
        on_wake: Callable[[str, AgentMessage], Coroutine[Any, Any, None]],
    ) -> None:
        self._project_path = project_path
        self._bus = bus
        self._issue_id = issue_id
        self._on_wake = on_wake
        self._running = False

    async def start(self) -> None:
        """Start monitoring the bus for messages to dormant agents."""
        self._running = True
        queue = await self._bus.tap(self._issue_id)
        while self._running:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if not isinstance(message, AgentMessage):
                continue

            recipient_id = message.recipient_id
            if recipient_id == "broadcast":
                continue

            try:
                instance = load_agent_instance(self._project_path, recipient_id)
                if instance.status == AgentStatus.DORMANT:
                    await self._on_wake(recipient_id, message)
            except FileNotFoundError:
                pass  # Not a known agent

    def stop(self) -> None:
        self._running = False
