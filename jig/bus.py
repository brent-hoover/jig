"""Async message bus with file-backed persistence."""

import asyncio
from pathlib import Path
from typing import Union

from pydantic import BaseModel

from jig.models import AgentMessage, Message
from jig.persistence import append_message


# Bus accepts both legacy Message and new AgentMessage types.
BusMessage = Union[Message, AgentMessage]


def _get_recipient(message: BusMessage) -> str:
    """Extract recipient from either Message or AgentMessage."""
    if hasattr(message, "recipient_id"):
        return message.recipient_id
    return message.recipient


class MessageBus:
    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path
        self._subscribers: dict[str, dict[str, asyncio.Queue[BusMessage]]] = {}

    async def subscribe(self, issue_id: str, subscriber_name: str) -> asyncio.Queue[BusMessage]:
        """Subscribe to messages for a given issue and subscriber name."""
        if issue_id not in self._subscribers:
            self._subscribers[issue_id] = {}
        if subscriber_name not in self._subscribers[issue_id]:
            self._subscribers[issue_id][subscriber_name] = asyncio.Queue()
        return self._subscribers[issue_id][subscriber_name]

    async def tap(self, issue_id: str) -> asyncio.Queue[BusMessage]:
        """Subscribe a tap that receives a copy of ALL messages for an issue."""
        if not hasattr(self, "_taps"):
            self._taps: dict[str, list[asyncio.Queue[BusMessage]]] = {}
        queue: asyncio.Queue[BusMessage] = asyncio.Queue()
        self._taps.setdefault(issue_id, []).append(queue)
        return queue

    async def publish(self, issue_id: str, message: BusMessage) -> None:
        """Persist a message to JSONL, then route to subscribers."""
        await asyncio.to_thread(append_message, self._project_path, issue_id, message)

        recipient = _get_recipient(message)
        subs = self._subscribers.get(issue_id, {})
        if recipient == "broadcast":
            for queue in subs.values():
                await queue.put(message)
        elif recipient in subs:
            await subs[recipient].put(message)

        # Also send to all taps
        for tap_queue in getattr(self, "_taps", {}).get(issue_id, []):
            await tap_queue.put(message)

    def replay(self, issue_id: str) -> list[Message]:
        """Replay all persisted messages for an issue from JSONL."""
        from jig.persistence import load_messages
        return load_messages(self._project_path, issue_id)
