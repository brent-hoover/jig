"""TypedBus — the bus compatibility adapter (Epic 2, task 4).

Wraps the existing :class:`~jig.store.bus.MessageBus` and accepts both the
legacy ``Message``/``dict`` (old string topics) and the new
:class:`~jig.substrate.events.TypedEvent`. Typed events are down-converted to
``Message`` and published through the wrapped bus, so existing subscribers and
the bus's own tests are unaffected.

Bones scope: the publish adapter plus subscribe/unsubscribe pass-throughs so
callers can use the typed bus in place of the raw one. MVP migrates the
orchestrator's publishes and subscribers onto typed events; Final removes the
string-topic path.
"""

from __future__ import annotations

import asyncio

from jig.store.bus import Message, MessageBus
from jig.substrate.events import TypedEvent


class TypedBus:
    """Adapter over ``MessageBus`` that publishes typed events and legacy
    messages alike."""

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus

    @property
    def bus(self) -> MessageBus:
        """The wrapped message bus, for callers still on the legacy API."""
        return self._bus

    async def publish(self, item: TypedEvent | Message | dict) -> str:
        if isinstance(item, TypedEvent):
            item = item.to_message()
        return await self._bus.publish(item)

    async def subscribe(self, topic: str) -> asyncio.Queue[Message]:
        return await self._bus.subscribe(topic)

    async def unsubscribe(self, topic: str, queue: asyncio.Queue[Message]) -> None:
        await self._bus.unsubscribe(topic, queue)
