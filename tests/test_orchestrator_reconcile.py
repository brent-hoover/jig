"""Orchestrator reconcile tick.

A running orchestrator holds a stale in-memory ticket map — it never re-reads
tickets.jsonl on its own, so a ticket appended by a separate process (the CLI /
standalone MCP) is invisible. The reconcile body reloads from disk, then runs
the ready-scan, so an externally-created-then-approved issue reaches dispatch.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jig.orchestrator import Orchestrator
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _ticket(title: str, status: TicketStatus) -> Ticket:
    return Ticket(
        work_type=WorkType.FEATURE,
        title=title,
        created_by="cli",
        description=TICKET_AC_PLACEHOLDER,
        status=status,
    )


async def _orchestrator_with_store(tmp_path: Path) -> tuple[Orchestrator, Path]:
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True)
    path = store_dir / "tickets.jsonl"
    orch = Orchestrator(tmp_path)
    orch.tickets = TicketStore(path)
    await orch.tickets.load()
    return orch, path


@pytest.mark.asyncio
async def test_reconcile_makes_external_append_visible(tmp_path: Path) -> None:
    orch, path = await _orchestrator_with_store(tmp_path)

    # A separate process appends a PROPOSED ticket directly to the JSONL.
    external = TicketStore(path)
    await external.load()
    ext_id = await external.create(_ticket("from cli", TicketStatus.PROPOSED))

    # The orchestrator's stale in-memory map has not seen it.
    assert await orch.tickets.get(ext_id) is None

    await orch._reconcile_external_tickets()

    # After reconcile it is visible (reloaded from disk). PROPOSED means it is
    # not dispatched — the real ready-scan ran without spawning anything.
    seen = await orch.tickets.get(ext_id)
    assert seen is not None and seen.status is TicketStatus.PROPOSED


@pytest.mark.asyncio
async def test_reconcile_runs_ready_scan(tmp_path: Path) -> None:
    orch, _ = await _orchestrator_with_store(tmp_path)
    orch._start_ready_tickets = AsyncMock()

    await orch._reconcile_external_tickets()

    orch._start_ready_tickets.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_schedule_dispatches_once(tmp_path: Path) -> None:
    """The reconcile tick is a second concurrent scheduler. Two overlapping
    schedules for the same ticket must dispatch it exactly once."""
    orch, _ = await _orchestrator_with_store(tmp_path)
    orch._update_ticket_status = AsyncMock()
    orch._run_ticket = AsyncMock()
    tid = await orch.tickets.create(_ticket("go", TicketStatus.OPEN))

    await asyncio.gather(orch._handle_schedule(tid), orch._handle_schedule(tid))

    assert orch._run_ticket.call_count == 1
    assert list(orch._running_tickets.keys()) == [tid]

    for task in orch._running_tickets.values():
        task.cancel()
