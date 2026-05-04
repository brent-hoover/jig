"""Mid-work tier promotion (Track F Final) — model + ladder + idempotency."""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import AutoEscalationTriggered
from jig.analytics.store import AnalyticsStore
from jig.auto_escalation import EscalationSignal
from jig.pm.tier_promotion import (
    PROMOTION_LADDER,
    TierPromotion,
    decide_promotion,
    promote_ticket_tier,
)
from jig.store.tickets import TicketStore
from jig.ticket import Size, Ticket, WorkType


# ---- fixtures -----------------------------------------------------------


@pytest.fixture
async def tickets(tmp_path: Path) -> TicketStore:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    return store


@pytest.fixture
async def analytics(tmp_path: Path) -> AnalyticsStore:
    store = AnalyticsStore(tmp_path / "analytics.jsonl")
    await store.load()
    return store


def _make_ticket(ticket_id: str, dev_tier: str = "standard") -> Ticket:
    return Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        size=Size.M,
        title="t",
        created_by="test",
        dev_tier=dev_tier,
    )


def _signal(kind: str = "repeated_same_failure") -> EscalationSignal:
    return EscalationSignal(
        kind=kind,  # type: ignore[arg-type]
        threshold_value=3,
        observed_value=3,
        detail="test",
    )


# ---- promotion ladder ----------------------------------------------------


def test_ladder_shape():
    """Ladder is exactly standard → senior → sa per design."""
    assert PROMOTION_LADDER == {"standard": "senior", "senior": "sa"}


def test_decide_promotion_no_signals_returns_none():
    assert decide_promotion([], "standard") is None


def test_decide_promotion_standard_to_senior():
    assert decide_promotion([_signal()], "standard") == "senior"


def test_decide_promotion_senior_to_sa():
    assert decide_promotion([_signal()], "senior") == "sa"


def test_decide_promotion_sa_returns_none():
    """SA tier is the top of the ladder — no auto-promotion past it."""
    assert decide_promotion([_signal()], "sa") is None


def test_decide_promotion_unknown_tier_returns_none():
    """Defensive: don't auto-promote from a tier we don't recognize."""
    assert decide_promotion([_signal()], "junior-dev") is None


# ---- promote_ticket_tier --------------------------------------------------


@pytest.mark.asyncio
async def test_promote_updates_ticket_dev_tier(tickets: TicketStore):
    await tickets.create(_make_ticket("t-1", dev_tier="standard"))
    record = await promote_ticket_tier(
        tickets, "t-1", "senior", reason="signal tripped"
    )
    assert isinstance(record, TierPromotion)
    assert record.ticket_id == "t-1"
    assert record.from_tier == "standard"
    assert record.to_tier == "senior"
    refreshed = await tickets.get("t-1")
    assert refreshed is not None and refreshed.dev_tier == "senior"


@pytest.mark.asyncio
async def test_promote_idempotent_when_already_at_target(tickets: TicketStore):
    """Promoting to the current tier is a no-op (record returned, no error)."""
    await tickets.create(_make_ticket("t-1", dev_tier="senior"))
    record = await promote_ticket_tier(
        tickets, "t-1", "senior", reason="re-checking"
    )
    assert record.from_tier == "senior"
    assert record.to_tier == "senior"


@pytest.mark.asyncio
async def test_promote_missing_ticket_raises(tickets: TicketStore):
    with pytest.raises(ValueError, match="not in store"):
        await promote_ticket_tier(tickets, "no-such-ticket", "senior", reason="x")


@pytest.mark.asyncio
async def test_promote_emits_auto_escalation_event(
    tickets: TicketStore, analytics: AnalyticsStore
):
    """When emitter + signal supplied, fires AutoEscalationTriggered."""
    await tickets.create(_make_ticket("t-1", dev_tier="standard"))
    emitter = EventEmitter(analytics)
    sig = _signal("repeated_same_failure")
    await promote_ticket_tier(
        tickets,
        "t-1",
        "senior",
        reason="threshold trip",
        signal=sig,
        emitter=emitter,
        agent_id="dev:abc",
        turns_at_trip=15,
    )
    await emitter.drain()
    events = await analytics.by_kind("auto_escalation_triggered")
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, AutoEscalationTriggered)
    assert ev.ticket_id == "t-1"
    assert ev.from_tier == "standard"
    assert ev.to_tier == "senior"
    assert ev.trip_signal == "repeated_same_failure"
    assert ev.turns_at_trip == 15


@pytest.mark.asyncio
async def test_promote_skips_event_without_signal(
    tickets: TicketStore, analytics: AnalyticsStore
):
    """Operator-driven promotion (no signal) skips the analytics event."""
    await tickets.create(_make_ticket("t-1", dev_tier="standard"))
    emitter = EventEmitter(analytics)
    await promote_ticket_tier(
        tickets,
        "t-1",
        "senior",
        reason="operator override",
        emitter=emitter,
        agent_id="dev:abc",
    )
    await emitter.drain()
    events = await analytics.by_kind("auto_escalation_triggered")
    assert events == []


# ---- signal-to-tier mapping (each signal kind) ---------------------------


@pytest.mark.parametrize(
    "signal_kind",
    [
        "repeated_same_failure",
        "tool_call_flailing",
        "no_commit_drift",
        "out_of_budget",
        "forced_reflection_at_minutes",
    ],
)
def test_each_signal_kind_triggers_promotion_from_standard(signal_kind: str):
    """Every escalation signal kind warrants promotion from standard tier."""
    sig = _signal(signal_kind)
    assert decide_promotion([sig], "standard") == "senior"


# ---- non-double-promotion (idempotency in one cycle) ---------------------


@pytest.mark.asyncio
async def test_two_promote_calls_in_one_cycle_only_advance_one_step(
    tickets: TicketStore,
):
    """Track F Final mid-cycle idempotency.

    A second ``promote_ticket_tier`` call with the same target during
    one cycle returns a record showing from_tier == to_tier so the
    Coordinator can detect "already promoted this cycle, don't double-
    apply." The dev_tier doesn't ratchet past the chosen target.
    """
    await tickets.create(_make_ticket("t-1", dev_tier="standard"))
    first = await promote_ticket_tier(tickets, "t-1", "senior", reason="r1")
    assert first.from_tier == "standard" and first.to_tier == "senior"

    second = await promote_ticket_tier(tickets, "t-1", "senior", reason="r2")
    assert second.from_tier == "senior" and second.to_tier == "senior"

    refreshed = await tickets.get("t-1")
    assert refreshed is not None and refreshed.dev_tier == "senior"
