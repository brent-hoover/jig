"""Tests for the v2 status-change callback hook on ``TicketStore``.

The orchestrator uses this to feed ``TicketStateChanged`` analytics events
without coupling the store to the analytics schema. Callback supports both
sync and async callables; ``from_state`` is None on initial transitions
(when the prior state isn't observable).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.mark.asyncio
async def test_callback_fires_on_status_change(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()

    seen: list[tuple[str, str | None, str]] = []

    def cb(ticket_id: str, prev: str | None, curr: str) -> None:
        seen.append((ticket_id, prev, curr))

    store.set_status_change_callback(cb)

    t = Ticket(
        work_type=WorkType.FEATURE,
        title="t",
        created_by="u",
        description=TICKET_AC_PLACEHOLDER,
    )
    tid = await store.create(t)
    # Create doesn't go through update — no callback fire on insert.
    assert seen == []

    await store.update_status(tid, TicketStatus.IN_PROGRESS)
    await store.update_status(tid, TicketStatus.RESOLVED)

    assert seen == [
        (tid, "open", "in_progress"),
        (tid, "in_progress", "resolved"),
    ]


@pytest.mark.asyncio
async def test_callback_skipped_when_status_unchanged(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()

    seen: list[tuple] = []
    store.set_status_change_callback(lambda tid, p, c: seen.append((tid, p, c)))

    t = Ticket(
        work_type=WorkType.FEATURE,
        title="t",
        created_by="u",
        description=TICKET_AC_PLACEHOLDER,
    )
    tid = await store.create(t)

    # Update title — no status field; callback must not fire.
    await store.update(tid, title="renamed")
    # Update with same status — also no fire.
    await store.update(tid, status=TicketStatus.OPEN)

    assert seen == []


@pytest.mark.asyncio
async def test_callback_skipped_when_field_omitted(tmp_path: Path) -> None:
    """``status`` not in fields — never read prev_status, never fire."""
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()

    seen: list = []
    store.set_status_change_callback(lambda *a: seen.append(a))

    t = Ticket(
        work_type=WorkType.FEATURE,
        title="t",
        created_by="u",
        description=TICKET_AC_PLACEHOLDER,
    )
    tid = await store.create(t)
    await store.update(tid, assignee="dev:x")

    assert seen == []


@pytest.mark.asyncio
async def test_async_callback_scheduled(tmp_path: Path) -> None:
    """Async callbacks are scheduled as a task — store doesn't await them."""
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()

    seen: list[str] = []

    async def cb(ticket_id: str, prev: str | None, curr: str) -> None:
        seen.append(curr)

    store.set_status_change_callback(cb)

    t = Ticket(
        work_type=WorkType.FEATURE,
        title="t",
        created_by="u",
        description=TICKET_AC_PLACEHOLDER,
    )
    tid = await store.create(t)
    await store.update_status(tid, TicketStatus.IN_PROGRESS)

    # Give the scheduled task a tick to run.
    await asyncio.sleep(0)
    assert seen == ["in_progress"]


@pytest.mark.asyncio
async def test_clearing_callback(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()

    seen: list = []
    store.set_status_change_callback(lambda *a: seen.append(a))
    store.set_status_change_callback(None)

    t = Ticket(
        work_type=WorkType.FEATURE,
        title="t",
        created_by="u",
        description=TICKET_AC_PLACEHOLDER,
    )
    tid = await store.create(t)
    await store.update_status(tid, TicketStatus.IN_PROGRESS)

    assert seen == []
