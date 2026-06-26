"""Typed bus events — the upgrade from magic-string topics + ``kind`` payloads.

Today the bus routes on a string ``topic`` and carries an untyped ``payload``
dict whose ``kind`` field discriminates the event. ``TypedEvent`` replaces that
with pydantic types: each concrete event declares its ``kind`` (matching the
existing string), its legacy ``MessageType``, and the routing ``topic``, and
renders itself as a legacy :class:`~jig.store.bus.Message` whose payload matches
what the current publishers emit byte-for-byte.

Bones scope: the two lifecycle events that actually travel on the **bus** and
that the orchestrator's ``"orchestrator"`` subscriber dispatches on —
``ticket_created`` and ``ticket_updated`` (both ``CONTEXT_UPDATE``, see
``jig/ticket_events.py`` and ``Orchestrator._update_ticket_status``).

Note: ``ticket_completed``/``ticket_failed`` are **not** bus messages — they are
``JigEvent``s emitted on the WebSocket :class:`~jig.events.EventEmitter`
(``Orchestrator._emit_ticket_failed`` etc.). Typing that separate channel is
out of scope here; status transitions to completed/failed reach the bus as
``ticket_updated`` events.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from jig.store.bus import Message, MessageType

_ENVELOPE_FIELDS = frozenset({"topic", "sender", "recipient", "correlation_id"})


def ticket_topic(ticket_id: str) -> str:
    """``tickets.<id>`` — the per-ticket topic subscribers fan out on."""
    return f"tickets.{ticket_id}"


class TypedEvent(BaseModel):
    """Base for typed bus events.

    ``kind`` is the discriminator (matching the legacy payload ``kind`` string)
    and ``message_type`` the legacy envelope type. The envelope fields
    (``topic``/``sender``/``recipient``/``correlation_id``) carry routing; every
    other model field is domain payload and is folded into the legacy message
    ``payload`` alongside ``kind`` by :meth:`to_message`.
    """

    model_config = ConfigDict(extra="forbid")

    kind: ClassVar[str]
    message_type: ClassVar[MessageType] = MessageType.CONTEXT_UPDATE

    topic: str = "orchestrator"
    sender: str = "orchestrator"
    recipient: str = "broadcast"
    correlation_id: str | None = None

    def _payload(self) -> dict[str, Any]:
        """The domain payload (sans ``kind``). Override to match a legacy shape."""
        return self.model_dump(exclude=set(_ENVELOPE_FIELDS))

    def to_message(self) -> Message:
        """Render as a legacy :class:`Message` (topic + ``kind``-tagged payload)."""
        return Message(
            sender=self.sender,
            to=self.recipient,
            type=self.message_type,
            payload={"kind": self.kind, **self._payload()},
            topic=self.topic,
            correlation_id=self.correlation_id,
        )


class TicketCreated(TypedEvent):
    """``ticket_created`` — faithful to ``jig.ticket_events._build_payload``."""

    kind: ClassVar[str] = "ticket_created"

    ticket_id: str
    title: str
    description: str = ""
    work_type: str
    size: str
    assignee: str | None = None
    parent_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    workflow: str | None = None
    status: str

    def _payload(self) -> dict[str, Any]:
        payload = super()._payload()
        # Legacy alias kept while older subscribers still read ``type``
        # (mirrors ``_build_payload``); drop in lockstep when the TUI migrates.
        payload["type"] = self.work_type
        return payload


class TicketUpdated(TypedEvent):
    """``ticket_updated`` — the status-transition event the service loop
    re-schedules on (``{kind, ticket_id, status}``)."""

    kind: ClassVar[str] = "ticket_updated"

    ticket_id: str
    status: str


__all__ = [
    "TicketCreated",
    "TicketUpdated",
    "TypedEvent",
    "ticket_topic",
]
