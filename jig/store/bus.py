import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import ConfigDict, Field

from jig.store.models import StoreModel, TypedCollection

_logger = logging.getLogger(__name__)


class MessageType(str, Enum):
    TASK_ASSIGNMENT = "task_assignment"
    TASK_COMPLETION = "task_completion"
    QUESTION = "question"
    ANSWER = "answer"
    CONTEXT_UPDATE = "context_update"
    STATUS = "status"


class Message(StoreModel):
    sender: str = Field(alias="from")
    to: str
    type: MessageType
    payload: dict
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str | None = None
    topic: str

    model_config = ConfigDict(populate_by_name=True)


class MessageBus:
    def __init__(self, path: Path) -> None:
        self._collection: TypedCollection[Message] = TypedCollection(
            path, model=Message, index_fields=["topic"]
        )
        self._subscribers: dict[str, list[asyncio.Queue[Message]]] = {}
        self._listeners: list[Callable[[Message], Awaitable[None]]] = []
        self._agent_subscriptions: dict[tuple[str, str], asyncio.Queue[Message]] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        await self._collection.load()

    async def publish(self, message: Message | dict) -> str:
        if isinstance(message, dict):
            message = Message.model_validate(message)
        msg_id = await self._collection.insert(message)

        async with self._lock:
            subs_snapshot = list(self._subscribers.get(message.topic, []))
            listeners_snapshot = list(self._listeners)

        for queue in subs_snapshot:
            await queue.put(message)
        for callback in listeners_snapshot:
            try:
                await callback(message)
            except Exception:
                _logger.warning(
                    "websocket listener raised during publish", exc_info=True
                )
        return msg_id

    async def subscribe(self, topic: str) -> asyncio.Queue[Message]:
        async with self._lock:
            queue: asyncio.Queue[Message] = asyncio.Queue()
            self._subscribers.setdefault(topic, []).append(queue)
            return queue

    async def subscribe_agent(
        self, topic: str, agent_id: str
    ) -> asyncio.Queue[Message]:
        """Return a stable per-agent subscription queue for a topic.

        Repeated calls with the same (topic, agent_id) pair return the
        same queue object so that messages published between calls are
        not dropped. This replaces the former module-level cache keyed
        on ``id(bus)`` in ``jig.mcp_tools``.
        """
        key = (topic, agent_id)
        async with self._lock:
            existing = self._agent_subscriptions.get(key)
            if existing is not None:
                return existing
            queue: asyncio.Queue[Message] = asyncio.Queue()
            self._subscribers.setdefault(topic, []).append(queue)
            self._agent_subscriptions[key] = queue
            return queue

    async def unsubscribe(self, topic: str, queue: asyncio.Queue[Message]) -> None:
        async with self._lock:
            queues = self._subscribers.get(topic)
            if not queues:
                return
            try:
                queues.remove(queue)
            except ValueError:
                return
            if not queues:
                del self._subscribers[topic]

    async def get_history(self, topic: str, limit: int = 100) -> list[Message]:
        results = await self._collection.find_where(topic=topic)
        results.sort(key=lambda m: m.timestamp)
        return results[-limit:]

    async def add_websocket_listener(
        self, callback: Callable[[Message], Awaitable[None]]
    ) -> None:
        async with self._lock:
            self._listeners.append(callback)
