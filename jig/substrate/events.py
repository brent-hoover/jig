"""Typed bus events — the upgrade from magic-string topics + ``kind`` payloads.

Today the bus routes on a string ``topic`` and carries an untyped ``payload``
dict whose ``kind`` field discriminates the event. ``TypedEvent`` replaces that
with pydantic types: each concrete event declares its ``kind`` (matching the
existing string so the down-conversion is faithful) and the routing ``topic``,
and knows how to render itself as a legacy :class:`~jig.store.bus.Message`.

Bones scope: the ticket-lifecycle events the orchestrator dispatches on. The
full event catalog (thread/check/spike/… events) lands incrementally; the base
class is the contract those will extend.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from jig.store.bus import Message, MessageType

_ENVELOPE_FIELDS = frozenset({"topic", "sender", "recipient", "correlation_id"})


def ticket_topic(ticket_id: str) -> str:
    """``tickets.<id>`` — the per-ticket topic subscribers fan out on."""
    return f"tickets.{ticket_id}"


class TypedEvent(BaseModel):
    """Base for typed bus events.

    ``kind`` is the discriminator (a class var matching the legacy payload
    ``kind`` string). The envelope fields (``topic``/``sender``/``recipient``/
    ``correlation_id``) carry routing; every other field is domain payload and
    is folded into the legacy message ``payload`` alongside ``kind``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ClassVar[str]

    topic: str = "orchestrator"
    sender: str = "orchestrator"
    recipient: str = "orchestrator"
    correlation_id: str | None = None

    def to_message(self) -> Message:
        """Render as a legacy :class:`Message` (topic + ``kind``-tagged payload)."""
        payload = {
            "kind": self.kind,
            **self.model_dump(exclude=set(_ENVELOPE_FIELDS)),
        }
        return Message(
            sender=self.sender,
            to=self.recipient,
            type=MessageType.STATUS,
            payload=payload,
            topic=self.topic,
            correlation_id=self.correlation_id,
        )


class TicketCreated(TypedEvent):
    kind: ClassVar[str] = "ticket_created"
    ticket_id: str


class TicketUpdated(TypedEvent):
    kind: ClassVar[str] = "ticket_updated"
    ticket_id: str


class TicketCompleted(TypedEvent):
    kind: ClassVar[str] = "ticket_completed"
    ticket_id: str


class TicketFailed(TypedEvent):
    kind: ClassVar[str] = "ticket_failed"
    ticket_id: str


__all__ = [
    "TicketCompleted",
    "TicketCreated",
    "TicketFailed",
    "TicketUpdated",
    "TypedEvent",
    "ticket_topic",
]
