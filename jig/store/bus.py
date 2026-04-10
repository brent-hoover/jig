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
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
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

    async def unsubscribe(
        self, topic: str, queue: asyncio.Queue[Message]
    ) -> None:
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

    async def get_history(
        self, topic: str, limit: int = 100
    ) -> list[Message]:
        results = await self._collection.find_where(topic=topic)
        results.sort(key=lambda m: m.timestamp)
        return results[-limit:]
