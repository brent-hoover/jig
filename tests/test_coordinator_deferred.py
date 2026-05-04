"""DEFERRED queue + Coordinator triage helpers (Track F MVP).

Per ``docs/pm-workflow/design.md`` §"DEFERRED queue triage": some
tickets get deferred mid-flight (e.g., a reviewer flagged a Notable
issue but the dev finished). Coordinator MVP creates entries; the
Planner triages at re-plan time. Mid-MVP triage is mechanical (no
LLM) — operator-driven decisions with the Coordinator suggesting an
action per entry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.coordinator import Coordinator
from jig.spec_loader import build_plan_path
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType


@pytest.fixture
async def store(tmp_path: Path) -> TicketStore:
    s = TicketStore(tmp_path / "tickets.jsonl")
    await s.load()
    return s


def _ticket(
    *,
    ticket_id: str = "tb-cat",
    status: TicketStatus = TicketStatus.OPEN,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title=ticket_id,
        created_by="planner-v2",
        status=status,
    )


# ---- defer_ticket -------------------------------------------------------


@pytest.mark.asyncio
async def test_defer_ticket_appends_entry_and_stamps_field(
    tmp_path: Path, store: TicketStore
):
    await store.create(_ticket())
    coord = Coordinator(tickets=store, project_root=tmp_path)

    await coord.defer_ticket("tb-cat", "notable comment", "fold in next pass")

    entries = coord.list_deferred()
    assert len(entries) == 1
    e = entries[0]
    assert e.ticket_id == "tb-cat"
    assert e.reason == "notable comment"
    assert e.notes == "fold in next pass"
    assert e.deferred_at is not None

    refreshed = await store.get("tb-cat")
    assert refreshed is not None
    assert refreshed.deferred_at is not None
    assert refreshed.deferred_at == e.deferred_at


@pytest.mark.asyncio
async def test_defer_ticket_persists_to_jsonl(
    tmp_path: Path, store: TicketStore
):
    await store.create(_ticket())
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.defer_ticket("tb-cat", "notable")

    queue_path = tmp_path / ".jig" / "plan" / "deferred-queue.jsonl"
    assert queue_path.is_file()
    assert "tb-cat" in queue_path.read_text()


@pytest.mark.asyncio
async def test_defer_ticket_handles_missing_ticket(
    tmp_path: Path, store: TicketStore
):
    """Operator may defer a ticket id the store doesn't have yet (e.g. planned but unmaterialized)."""
    coord = Coordinator(tickets=store, project_root=tmp_path)
    # Should not raise — defer the queue entry, skip the field stamp.
    await coord.defer_ticket("not-yet-created", "reserve")
    assert coord.list_deferred()[0].ticket_id == "not-yet-created"


@pytest.mark.asyncio
async def test_list_deferred_returns_chronological(
    tmp_path: Path, store: TicketStore
):
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.defer_ticket("a", "first")
    await coord.defer_ticket("b", "second")
    await coord.defer_ticket("c", "third")
    ids = [e.ticket_id for e in coord.list_deferred()]
    assert ids == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_list_deferred_empty_when_no_queue(
    tmp_path: Path, store: TicketStore
):
    coord = Coordinator(tickets=store, project_root=tmp_path)
    assert coord.list_deferred() == []


# ---- triage_deferred ----------------------------------------------------


@pytest.mark.asyncio
async def test_triage_recommends_rematerialize_when_resolved(
    tmp_path: Path, store: TicketStore
):
    await store.create(_ticket(status=TicketStatus.RESOLVED))
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.defer_ticket("tb-cat", "notable")

    decisions = await coord.triage_deferred(build_plan_path(tmp_path))

    assert len(decisions) == 1
    assert decisions[0].recommended_action == "rematerialize"


@pytest.mark.asyncio
async def test_triage_recommends_close_when_ticket_missing(
    tmp_path: Path, store: TicketStore
):
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.defer_ticket("ghost", "deferred but no ticket")

    decisions = await coord.triage_deferred(build_plan_path(tmp_path))
    assert decisions[0].recommended_action == "close"


@pytest.mark.asyncio
async def test_triage_recommends_leave_deferred_for_in_flight(
    tmp_path: Path, store: TicketStore
):
    await store.create(_ticket(status=TicketStatus.IN_PROGRESS))
    coord = Coordinator(tickets=store, project_root=tmp_path)
    await coord.defer_ticket("tb-cat", "notable")

    decisions = await coord.triage_deferred(build_plan_path(tmp_path))
    assert decisions[0].recommended_action == "leave_deferred"


@pytest.mark.asyncio
async def test_triage_returns_empty_for_empty_queue(
    tmp_path: Path, store: TicketStore
):
    coord = Coordinator(tickets=store, project_root=tmp_path)
    assert await coord.triage_deferred(build_plan_path(tmp_path)) == []


# ---- Ticket.deferred_at field ------------------------------------------


def test_ticket_deferred_at_defaults_none():
    t = Ticket(
        id="x", work_type=WorkType.FEATURE, title="x", created_by="me"
    )
    assert t.deferred_at is None


def test_ticket_deferred_at_round_trips_via_model_dump():
    """Backward compat: existing JSONL records without deferred_at must still load."""
    raw = {
        "_id": "x",
        "work_type": "feature",
        "title": "x",
        "created_by": "me",
    }
    t = Ticket.model_validate(raw)
    assert t.deferred_at is None
