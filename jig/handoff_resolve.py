"""Shared post-handoff ticket resolution.

Init-flow handoff handlers (``po_finish_brief``, ``spec_publish``,
``spec_report_gaps``, ``sa_propose_scaffold``, ``l0_finalize``,
``l3_finalize``) all share the same problem: posting the Handoff entry
alone leaves the ticket in IN_PROGRESS, which is NOT in the agent
loop's terminal-status set (``RESOLVED`` / ``BLOCKED`` / ``NEEDS_INFO``).
The agent then sits idle indefinitely instead of exiting and yielding
to the next phase. Flipping to RESOLVED + publishing ``ticket_updated``
on the ticket topic gives the agent loop both polling and bus paths
to notice it's done within one tick.

Originally lived in ``jig.init_mcp`` and was copy-pasted into
``jig.po_l0_mcp``. Centralized here so future v2 PO/SA handlers reuse
one implementation.
"""

from __future__ import annotations

from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import SystemEvent
from jig.ticket import TicketStatus


async def resolve_after_handoff(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    ticket_id: str,
    author: str,
) -> None:
    """Mark ``ticket_id`` RESOLVED and broadcast it.

    No-op when the ticket is already RESOLVED or doesn't exist — the
    handler is meant to be safely repeatable in case a follow-on emit
    races with the ticket update.
    """
    current = await tickets.get(ticket_id)
    if current is None or current.status == TicketStatus.RESOLVED:
        return
    updated = await tickets.update(ticket_id, status=TicketStatus.RESOLVED)
    await threads.post(
        SystemEvent(
            ticket_id=ticket_id,
            author=author,
            event_type="status_change",
            content=f"status {current.status.value} -> {updated.status.value}",
        )
    )
    await bus.publish(
        Message(
            sender=author,
            to=updated.assignee or "broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "ticket_updated",
                "ticket_id": ticket_id,
                "status": updated.status.value,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
