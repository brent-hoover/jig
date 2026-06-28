"""Orchestrator service-loop message dispatch (`_handle_service_message`).

The `"orchestrator"`-topic dispatcher drives scheduling off typed lifecycle
events, with a raw-field fallback for partial/legacy payloads. A malformed
payload must never crash the loop — notably an unhashable id must not reach the
scheduler (which keys `_running_tickets` by id).
"""

from __future__ import annotations

import pytest

from jig.orchestrator import Orchestrator
from jig.store.bus import Message, MessageType
from jig.substrate.events import TicketCreated, TicketUpdated


def _msg(payload: dict, topic: str = "orchestrator") -> Message:
    return Message(
        sender="x",
        to="orchestrator",
        type=MessageType.CONTEXT_UPDATE,
        payload=payload,
        topic=topic,
    )


@pytest.fixture
def orch(tmp_path, monkeypatch):
    o = Orchestrator(project_path=tmp_path)
    o._running = True
    scheduled: list[str] = []
    rescheduled: list[str] = []

    async def _sched(ticket_id):
        scheduled.append(ticket_id)

    async def _resched(ticket_id):
        rescheduled.append(ticket_id)

    monkeypatch.setattr(o, "_handle_schedule", _sched)
    monkeypatch.setattr(o, "_reschedule_reset_ticket", _resched)
    o._scheduled = scheduled  # type: ignore[attr-defined]
    o._rescheduled = rescheduled  # type: ignore[attr-defined]
    return o


async def test_typed_created_schedules(orch) -> None:
    await orch._handle_service_message(
        TicketCreated(
            ticket_id="jig-1", title="T", work_type="feature", size="s", status="open"
        ).to_message()
    )
    assert orch._scheduled == ["jig-1"]


async def test_typed_updated_open_reschedules(orch) -> None:
    await orch._handle_service_message(
        TicketUpdated(ticket_id="jig-1", status="open").to_message()
    )
    assert orch._rescheduled == ["jig-1"]


async def test_raw_partial_created_still_schedules(orch) -> None:
    # Undecodable typed payload (missing required fields) -> raw fallback.
    await orch._handle_service_message(
        _msg({"kind": "ticket_created", "ticket_id": "jig-9"})
    )
    assert orch._scheduled == ["jig-9"]


async def test_malformed_unhashable_id_does_not_crash_or_schedule(orch) -> None:
    # A list id would crash the scheduler (it keys _running_tickets by id); the
    # dispatcher must drop it, not hand it on.
    await orch._handle_service_message(
        _msg({"kind": "ticket_updated", "ticket_id": ["x"], "status": "open"})
    )
    await orch._handle_service_message(
        _msg({"kind": "ticket_created", "ticket_id": {"bad": 1}})
    )
    assert orch._scheduled == []
    assert orch._rescheduled == []


async def test_shutdown_request_stops_the_loop(orch) -> None:
    assert orch._running is True
    await orch._handle_service_message(_msg({"kind": "shutdown_request"}))
    assert orch._running is False


async def test_unknown_kind_is_ignored(orch) -> None:
    await orch._handle_service_message(_msg({"kind": "comment_posted", "x": 1}))
    assert orch._scheduled == []
    assert orch._rescheduled == []
