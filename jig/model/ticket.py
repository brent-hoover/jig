"""Ticket domain types, exposed under the model layer.

Bones phase: a re-export seam over ``jig/ticket.py`` so consumers can begin
importing ``Ticket`` from ``jig.model``. The definitions still live in
``jig/ticket.py`` for compat; the real move (and shim removal) happens in
MVP/Final.
"""

from __future__ import annotations

from jig.ticket import (
    Size,
    Ticket,
    TicketPlanMetadata,
    TicketStatus,
    TicketTouches,
    WorkType,
)

__all__ = [
    "Size",
    "Ticket",
    "TicketPlanMetadata",
    "TicketStatus",
    "TicketTouches",
    "WorkType",
]
