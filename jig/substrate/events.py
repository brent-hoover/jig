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

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    @classmethod
    def from_message(cls, msg: Message) -> "TypedEvent":
        """Reconstruct the typed event from a legacy :class:`Message` — the
        inverse of :meth:`to_message`. Only concrete events implement it;
        decoding is dispatched by ``kind`` via :func:`decode_event`."""
        raise NotImplementedError(f"{cls.__name__} cannot decode a Message")

    @staticmethod
    def _envelope(msg: Message) -> dict[str, Any]:
        """The routing envelope of a received message, as ``from_message`` kwargs."""
        return {
            "topic": msg.topic,
            "sender": msg.sender,
            "recipient": msg.to,
            "correlation_id": msg.correlation_id,
        }


class _TicketLifecycleEvent(TypedEvent):
    """Shared base for per-ticket lifecycle events.

    The real publishers fan each event to the per-ticket ``tickets.<id>`` topic
    (TUI + agent subscribers) and, for the dispatch copy, to ``"orchestrator"``.
    So the default topic here is the per-ticket one — a bare event routes to the
    broad audience; the orchestrator-dispatch copy is an explicit
    ``topic="orchestrator"``. This avoids silently dropping per-ticket
    subscribers on migration.
    """

    ticket_id: str
    # Empty sentinel (not ``str | None``) keeps the annotation ``str`` — the
    # validator fills the per-ticket default before any caller sees it.
    topic: str = Field(default="")

    @model_validator(mode="after")
    def _default_to_per_ticket_topic(self) -> "_TicketLifecycleEvent":
        if not self.topic:
            self.topic = ticket_topic(self.ticket_id)
        return self


class TicketCreated(_TicketLifecycleEvent):
    """``ticket_created`` — faithful to ``jig.ticket_events._build_payload``."""

    kind: ClassVar[str] = "ticket_created"

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

    @classmethod
    def from_message(cls, msg: Message) -> "TicketCreated":
        p = msg.payload or {}
        # ``type`` is the derived legacy alias of ``work_type`` — don't feed it
        # back (extra="forbid"); reconstruct from the model fields only.
        return cls(
            **cls._envelope(msg),
            ticket_id=p["ticket_id"],
            title=p["title"],
            description=p.get("description", ""),
            work_type=p["work_type"],
            size=p["size"],
            assignee=p.get("assignee"),
            parent_id=p.get("parent_id"),
            depends_on=p.get("depends_on", []),
            workflow=p.get("workflow"),
            status=p["status"],
        )


class TicketUpdated(_TicketLifecycleEvent):
    """``ticket_updated`` — the status-transition event the service loop
    re-schedules on (``{kind, ticket_id, status}``).

    ``internal`` carries the legacy ``_internal`` marker: when an agent (not the
    orchestrator) signals a terminal status, the update still travels on the bus
    so the agent runner sees it, but the emitter relay skips it so the TUI does
    not flicker (see ``ticket_mcp`` and ``Orchestrator`` emitter relay). It is
    serialized as ``_internal: True`` only when set, matching the publishers.
    """

    kind: ClassVar[str] = "ticket_updated"

    status: str
    internal: bool = False

    def _payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ticket_id": self.ticket_id, "status": self.status}
        if self.internal:
            payload["_internal"] = True
        return payload

    @classmethod
    def from_message(cls, msg: Message) -> "TicketUpdated":
        p = msg.payload or {}
        return cls(
            **cls._envelope(msg),
            ticket_id=p["ticket_id"],
            status=p["status"],
            internal=bool(p.get("_internal", False)),
        )


# kind -> concrete event, for decoding received messages back into typed events.
_BY_KIND: dict[str, type[TypedEvent]] = {
    TicketCreated.kind: TicketCreated,
    TicketUpdated.kind: TicketUpdated,
}


def decode_event(msg: Message) -> TypedEvent | None:
    """Reconstruct the typed event a legacy :class:`Message` carries.

    Returns ``None`` for a payload whose ``kind`` is outside the typed set
    (``shutdown_request``, ``comment_posted``, …) so subscribers can fall back
    to raw-payload handling for events not yet migrated."""
    kind = (msg.payload or {}).get("kind")
    decoder = _BY_KIND.get(kind) if isinstance(kind, str) else None
    return None if decoder is None else decoder.from_message(msg)


__all__ = [
    "TicketCreated",
    "TicketUpdated",
    "TypedEvent",
    "decode_event",
    "ticket_topic",
]
