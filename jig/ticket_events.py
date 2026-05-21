"""Shared bus-event publishers for ticket lifecycle events.

Centralises the ``ticket_created`` payload shape so every code path
that mints a Ticket (PM MCP, orchestrator, init flow, CLI, TUI
commands) produces the same envelope. Without this, subscribers had
to defensively merge partial event payloads against a snapshot
baseline — and any creation path that bypassed
``ticket_mcp.handle_create_ticket`` left the TUI with a half-empty
ticket dict (no title, no work_type, no size — just ``id`` from the
first status-change update that arrived later).

This module is intentionally thin: callers always pass an already-
created ``Ticket`` plus a bus. The helpers compose the payload from
the model and publish to the standard topics.

Topics published:

* ``orchestrator`` (for the dispatch loop)
* ``tickets.{ticket_id}`` (broadcast for any topic subscriber, e.g. TUI)
"""

from __future__ import annotations

from jig.store.bus import Message, MessageBus, MessageType
from jig.ticket import Ticket


def _build_payload(ticket: Ticket, *, depends_on: list[str] | None = None) -> dict:
    """Compose the ``ticket_created`` payload from a Ticket model.

    The shape mirrors what ``ticket_mcp.handle_create_ticket`` emits
    so TUI subscribers see a uniform envelope regardless of which
    creation path produced the ticket. Fields included are the
    minimum every consumer needs to render a meaningful entry
    without round-tripping back to the store.
    """
    return {
        "kind": "ticket_created",
        "ticket_id": ticket.id,
        "title": ticket.title,
        "description": ticket.description,
        "work_type": ticket.work_type.value,
        # Legacy alias kept while older subscribers still read ``type``.
        # Drop in lockstep with ``ticket_mcp.handle_create_ticket`` once
        # the TUI fully migrates to ``work_type``.
        "type": ticket.work_type.value,
        "size": ticket.size.value,
        "assignee": ticket.assignee,
        "parent_id": ticket.parent_id,
        "depends_on": list(depends_on) if depends_on else list(ticket.blocked_by),
        "workflow": ticket.workflow,
        "status": ticket.status.value,
    }


async def publish_ticket_created(
    bus: MessageBus,
    ticket: Ticket,
    *,
    sender: str = "system",
    depends_on: list[str] | None = None,
    for_dispatch: bool = False,
) -> None:
    """Announce a freshly-created ticket on the bus.

    Always publishes to the broadcast topic ``tickets.{ticket_id}``
    so subscribers (TUI, log readers) maintain complete in-memory
    state. Optionally publishes to the ``orchestrator`` topic when
    ``for_dispatch=True``, which is what triggers
    ``Orchestrator._handle_schedule`` for the ticket.

    Default is broadcast-only. Two creation surfaces want the
    dispatch trigger:

    * ``ticket_mcp.handle_create_ticket`` — PM agent created a
      work ticket and expects it to be scheduled immediately.
    * Other explicit-create paths that need to retain that
      behavior (rare).

    Direct-create paths (system tickets like brief/planning, the
    Coordinator's bones-layer materialize, spike proposals) leave
    ``for_dispatch=False``. They're either:

    * not subject to workflow dispatch (system tickets), or
    * scheduled via the orchestrator's polling
      ``_start_ready_tickets`` on the next cycle.

    Without this split, the store-level on-create callback that
    fires on every direct create would race with the orchestrator's
    own dispatch logic and double-trigger scheduling.

    ``sender`` identifies the creator for audit purposes (e.g.
    ``"cli"``, ``"orchestrator"``, ``"init"``). ``depends_on`` is the
    explicit dependency list from the creation path; falls back to
    ``ticket.blocked_by`` when omitted.
    """
    payload = _build_payload(ticket, depends_on=depends_on)
    if for_dispatch:
        await bus.publish(
            Message(
                sender=sender,
                to=ticket.assignee or "orchestrator",
                type=MessageType.CONTEXT_UPDATE,
                payload=payload,
                topic="orchestrator",
            )
        )
    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload=payload,
            topic=f"tickets.{ticket.id}",
        )
    )


def wire_create_publisher(tickets, bus: MessageBus, *, sender: str = "system") -> None:
    """Register a broadcast-only ``ticket_created`` publisher on ``tickets``.

    Every ``TicketStore`` instance that will see direct
    ``tickets.create(...)`` calls — orchestrator, init flow, CLI
    subcommands, TUI commands, the Coordinator — should call this
    helper once at construction so the broadcast topic
    ``tickets.{id}`` is populated for any subscriber (the TUI is the
    main one). Without the wire-up, the TUI sees only partial event
    payloads and renders ``(untitled)`` for any ticket it didn't
    receive a snapshot baseline for.

    The callback publishes broadcast-only by default
    (``for_dispatch=False``) — dispatch is the exclusive domain of
    ``handle_create_ticket``. Direct-create paths schedule via
    ``_start_ready_tickets`` polling, not bus events.

    Idempotent: re-calling overwrites the previous callback. Safe to
    call at any point during the store's lifetime.
    """

    async def _publish(ticket: Ticket) -> None:
        await publish_ticket_created(bus, ticket, sender=sender)

    tickets.set_create_callback(_publish)


__all__ = ["publish_ticket_created", "wire_create_publisher"]
