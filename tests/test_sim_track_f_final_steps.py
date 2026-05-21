"""Sim driver step handlers for Track F Final."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.pm.calibration import CalibrationStore
from jig.sim.driver import (
    DriverContext,
    _handle_invoke_calibration_record,
    _handle_invoke_tier_promotion,
)
from jig.sim.scenario import ScenarioStep, StepKind
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.analytics.emitter import EventEmitter
from jig.analytics.store import AnalyticsStore
from jig.ticket import Size, Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _step(kind: StepKind, **params) -> ScenarioStep:
    return ScenarioStep(kind=kind, params=params)


@pytest.fixture
async def driver_ctx(tmp_path: Path) -> DriverContext:
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    bus = MessageBus(store_dir / "messages.jsonl")
    analytics = AnalyticsStore(store_dir / "analytics.jsonl")
    for s in (tickets, threads, bus, analytics):
        await s.load()
    emitter = EventEmitter(analytics, simulator_mode=True)
    return DriverContext(
        project_root=tmp_path,
        tickets=tickets,
        threads=threads,
        bus=bus,
        analytics=analytics,
        emitter=emitter,
    )


# ---- INVOKE_TIER_PROMOTION -----------------------------------------------


@pytest.mark.asyncio
async def test_tier_promotion_step_promotes_standard_to_senior(
    driver_ctx: DriverContext,
):
    await driver_ctx.tickets.create(
        Ticket(
            id="tb-cat",
            work_type=WorkType.FEATURE,
            size=Size.M,
            title="t",
            created_by="t",
            dev_tier="standard",
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    await _handle_invoke_tier_promotion(
        driver_ctx,
        _step(StepKind.INVOKE_TIER_PROMOTION, ticket_id="tb-cat"),
    )
    refreshed = await driver_ctx.tickets.get("tb-cat")
    assert refreshed is not None
    assert refreshed.dev_tier == "senior"
    assert driver_ctx.last_tier_promotion_from == "standard"
    assert driver_ctx.last_tier_promotion_to == "senior"


@pytest.mark.asyncio
async def test_tier_promotion_step_missing_ticket_raises(
    driver_ctx: DriverContext,
):
    with pytest.raises(RuntimeError, match="missing"):
        await _handle_invoke_tier_promotion(
            driver_ctx,
            _step(StepKind.INVOKE_TIER_PROMOTION, ticket_id="t-x"),
        )


@pytest.mark.asyncio
async def test_tier_promotion_step_no_op_at_top_of_ladder(
    driver_ctx: DriverContext,
):
    """SA tier doesn't auto-promote — handler stamps no_change values."""
    await driver_ctx.tickets.create(
        Ticket(
            id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            title="t",
            created_by="t",
            dev_tier="sa",
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    await _handle_invoke_tier_promotion(
        driver_ctx,
        _step(StepKind.INVOKE_TIER_PROMOTION, ticket_id="t-1"),
    )
    refreshed = await driver_ctx.tickets.get("t-1")
    assert refreshed is not None
    assert refreshed.dev_tier == "sa"
    assert driver_ctx.last_tier_promotion_from == "sa"
    assert driver_ctx.last_tier_promotion_to == "sa"


# ---- INVOKE_CALIBRATION_RECORD -------------------------------------------


@pytest.mark.asyncio
async def test_calibration_record_step_persists_sample(
    driver_ctx: DriverContext,
):
    await _handle_invoke_calibration_record(
        driver_ctx,
        _step(
            StepKind.INVOKE_CALIBRATION_RECORD,
            ticket_id="t-1",
            size="m",
            observed_turns=25,
            observed_cost_usd=1.5,
        ),
    )
    store = CalibrationStore(driver_ctx.project_root)
    await store.load()
    samples = store.all()
    assert len(samples) == 1
    assert samples[0].ticket_id == "t-1"
    assert samples[0].size == "m"
    assert samples[0].observed_turns == 25
    assert samples[0].observed_cost_usd == 1.5
    assert driver_ctx.last_calibration_sample_size == "m"


@pytest.mark.asyncio
async def test_calibration_record_requires_size(driver_ctx: DriverContext):
    with pytest.raises(ValueError, match="size"):
        await _handle_invoke_calibration_record(
            driver_ctx,
            _step(StepKind.INVOKE_CALIBRATION_RECORD, ticket_id="t-1"),
        )


# ---- canonical coverage tags ---------------------------------------------


def test_canonical_tags_contains_track_f_final():
    from jig.sim.coverage import CANONICAL_TAGS

    expected = {
        "tier-promotion",
        "estimation-calibration",
        "bones-first-override",
        "cycle-view",
    }
    assert expected.issubset(CANONICAL_TAGS)
