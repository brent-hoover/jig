"""TypedBus — the bus compatibility adapter (Epic 2, task 4).

Wraps the existing :class:`~jig.store.bus.MessageBus` and accepts both the
legacy ``Message``/``dict`` (old string topics) and the new
:class:`~jig.substrate.events.TypedEvent`. Typed events are down-converted to
``Message`` and published through the wrapped bus, so existing subscribers and
the bus's own tests are unaffected.

Bones scope: ``publish`` is overridden to down-convert typed events; every
other ``MessageBus`` method (``subscribe``, ``unsubscribe``, ``subscribe_agent``,
``get_history``, ``recent``, ``add_websocket_listener``, ``load``, …) is
delegated unchanged via ``__getattr__`` so the adapter is a drop-in for the raw
bus. MVP migrates the orchestrator's publishes and subscribers onto typed
events; Final removes the string-topic path.
"""

from __future__ import annotations

from typing import Any

from jig.store.bus import Message, MessageBus
from jig.substrate.events import TypedEvent


class TypedBus:
    """Adapter over ``MessageBus`` that publishes typed events and legacy
    messages alike, and otherwise behaves exactly like the wrapped bus."""

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

    def __getattr__(self, name: str) -> Any:
        # Delegate any non-overridden MessageBus attribute to the wrapped bus.
        # ``_bus`` is set in __init__, so this only fires for genuine
        # pass-throughs — guard against pre-init / pickle lookups.
        if name == "_bus":
            raise AttributeError(name)
        return getattr(self._bus, name)
