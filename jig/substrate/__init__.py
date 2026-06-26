"""Substrate — Store + Bus coordination layer.

Sits above the raw JSONL store and message bus, below the engines. Exposes the
project's durable artifacts through a single ``StoreAuthority`` keyed on
``project://`` URIs, and a typed event bus (``TypedBus`` + ``TypedEvent``) that
replaces magic-string topics. Both wrap the existing store/bus — no new
persistence.
"""

from __future__ import annotations

from jig.substrate.bus import TypedBus
from jig.substrate.events import (
    TicketCreated,
    TicketUpdated,
    TypedEvent,
    ticket_topic,
)
from jig.substrate.store_authority import StoreAuthority

__all__ = [
    "StoreAuthority",
    "TicketCreated",
    "TicketUpdated",
    "TypedBus",
    "TypedEvent",
    "ticket_topic",
]
