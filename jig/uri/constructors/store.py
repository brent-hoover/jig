"""Constructors for ``project://store/...`` URIs (runtime JSONL stores)."""
from __future__ import annotations

from jig.uri.constructors._segments import seg


def store_ticket_uri(ticket_id: str) -> str:
    """``project://store/tickets/<id>`` — one ticket from tickets.jsonl."""
    return f"project://store/tickets/{seg(ticket_id, 'ticket_id')}"


def store_thread_uri(ticket_id: str) -> str:
    """``project://store/threads/<ticket-id>`` — thread for a ticket."""
    return f"project://store/threads/{seg(ticket_id, 'ticket_id')}"


def store_thread_entry_uri(ticket_id: str, entry_id: str) -> str:
    """``project://store/threads/<ticket-id>/entries/<entry-id>``."""
    return (
        f"project://store/threads/{seg(ticket_id, 'ticket_id')}"
        f"/entries/{seg(entry_id, 'entry_id')}"
    )


def store_event_uri(event_id: str) -> str:
    """``project://store/events/<event-id>`` — one analytics event by id."""
    return f"project://store/events/{seg(event_id, 'event_id')}"
