"""The daemon API contract — the only thing the edge (CLI/TUI) depends on.

The edge is a thin client: it sends **commands** to the daemon, renders
**snapshots**, and reacts to pushed **events**. It never imports engines, stores,
the orchestrator, or the agent — all of that lives behind the daemon, reached
only through ``DaemonApi``. Snapshots and events carry plain DTOs (``TicketView``),
not engine objects, so this module is dependency-light by construction (stdlib
only).

Bones defines the protocol + a representative command/event/DTO set. MVP routes
all CLI/TUI access through ``DaemonApi`` (see ``architecture/edge-audit.md`` for
the direct-access violations to retire) and localizes persona variation here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Snapshot DTOs — plain renderable views, never engine objects.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TicketView:
    """A renderable ticket — id, title, status. Not the engine ``Ticket``."""

    id: str
    title: str
    status: str


@dataclass(frozen=True)
class Snapshot:
    """A point-in-time view the edge renders."""

    tickets: tuple[TicketView, ...]


# ---------------------------------------------------------------------------
# Commands — edge -> daemon instructions.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """Base for an instruction the edge sends to the daemon."""


@dataclass(frozen=True)
class StartProject(Command):
    """Begin a run."""


@dataclass(frozen=True)
class ApproveTicket(Command):
    """Approve a proposed ticket so the daemon may dispatch it."""

    ref: str


# ---------------------------------------------------------------------------
# Events — daemon -> edge pushes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """Base for a state change the daemon pushes to the edge."""


@dataclass(frozen=True)
class TicketChanged(Event):
    """A ticket's status changed."""

    ticket_id: str
    status: str


# ---------------------------------------------------------------------------
# The contract.
# ---------------------------------------------------------------------------


@runtime_checkable
class DaemonApi(Protocol):
    """What the edge talks to. The daemon implements it over engines + stores."""

    async def send(self, command: Command) -> None: ...

    async def snapshot(self) -> Snapshot: ...

    def events(self) -> AsyncIterator[Event]: ...
