"""EDGE — the thin client layer (CLI / TUI / daemon front door).

The edge talks to the daemon only through the ``api`` contract
(command/event/snapshot); it never reaches into engines or stores. Bones defines
the contract; MVP routes all CLI/TUI access through it and localizes persona
variation. Kept dependency-light — importing the edge pulls in no engine module.
"""

from __future__ import annotations

from jig.edge.api import (
    ApproveTicket,
    Command,
    DaemonApi,
    Event,
    Snapshot,
    StartProject,
    TicketChanged,
    TicketView,
)

__all__ = [
    "ApproveTicket",
    "Command",
    "DaemonApi",
    "Event",
    "Snapshot",
    "StartProject",
    "TicketChanged",
    "TicketView",
]
