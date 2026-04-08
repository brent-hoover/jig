"""Event system for broadcasting workflow state to WebSocket clients."""

import asyncio
import json
from dataclasses import dataclass, field


@dataclass
class JigEvent:
    type: str
    data: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"type": self.type, "data": self.data})


class EventEmitter:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[JigEvent]] = []

    def subscribe(self) -> asyncio.Queue[JigEvent]:
        queue: asyncio.Queue[JigEvent] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[JigEvent]) -> None:
        self._subscribers.remove(queue)

    async def emit(self, event: JigEvent) -> None:
        for queue in self._subscribers:
            await queue.put(event)
