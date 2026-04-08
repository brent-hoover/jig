"""Async message bus with file-backed persistence."""

import asyncio
from pathlib import Path

from jig.models import Message
from jig.persistence import append_message


class MessageBus:
    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path
        self._subscribers: dict[str, dict[str, asyncio.Queue[Message]]] = {}

    async def subscribe(self, issue_id: str, subscriber_name: str) -> asyncio.Queue[Message]:
        """Subscribe to messages for a given issue and subscriber name."""
        if issue_id not in self._subscribers:
            self._subscribers[issue_id] = {}
        if subscriber_name not in self._subscribers[issue_id]:
            self._subscribers[issue_id][subscriber_name] = asyncio.Queue()
        return self._subscribers[issue_id][subscriber_name]

    async def publish(self, issue_id: str, message: Message) -> None:
        """Persist a message to JSONL, then route to subscribers."""
        await asyncio.to_thread(append_message, self._project_path, issue_id, message)

        subs = self._subscribers.get(issue_id, {})
        if message.recipient == "broadcast":
            for queue in subs.values():
                await queue.put(message)
        elif message.recipient in subs:
            await subs[message.recipient].put(message)

    def replay(self, issue_id: str) -> list[Message]:
        """Replay all persisted messages for an issue from JSONL."""
        from jig.persistence import load_messages
        return load_messages(self._project_path, issue_id)
