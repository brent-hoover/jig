"""EDGE bones — the daemon API contract (Epic 9, task 1).

The edge (CLI/TUI) talks to the daemon only through this command/event/snapshot
protocol — never by reaching into engines or stores. Snapshots/events carry
plain DTOs (``TicketView``), not engine objects, so the contract is the whole of
what the edge depends on.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

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


def test_snapshot_holds_plain_ticket_dtos() -> None:
    snap = Snapshot(tickets=(TicketView(id="jig-1", title="Login", status="open"),))
    assert snap.tickets[0].id == "jig-1"
    assert snap.tickets[0].status == "open"


def test_commands_share_a_base() -> None:
    assert isinstance(ApproveTicket(ref="jig-1"), Command)
    assert isinstance(StartProject(), Command)


def test_events_share_a_base() -> None:
    assert isinstance(TicketChanged(ticket_id="jig-1", status="resolved"), Event)


async def test_a_daemon_api_impl_satisfies_the_protocol() -> None:
    class _FakeDaemon:
        def __init__(self) -> None:
            self.sent: list[Command] = []

        async def send(self, command: Command) -> None:
            self.sent.append(command)

        async def snapshot(self) -> Snapshot:
            return Snapshot(tickets=())

        async def events(self) -> AsyncGenerator[Event, None]:
            return
            yield  # make it an async generator

    daemon = _FakeDaemon()
    assert isinstance(daemon, DaemonApi)

    await daemon.send(ApproveTicket(ref="jig-1"))
    assert daemon.sent == [ApproveTicket(ref="jig-1")]
    assert await daemon.snapshot() == Snapshot(tickets=())
    events = [e async for e in daemon.events()]
    assert events == []
