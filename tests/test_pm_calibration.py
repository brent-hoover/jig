"""Estimation calibration loop (Track F Final) — store + envelopes + emit."""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import (
    AgentSpawned,
    EstimationCalibrationUpdated,
    ToolCalled,
)
from jig.analytics.store import AnalyticsStore
from jig.auto_escalation import AutoEscalationConfig, check_escalation_signals
from jig.pm.calibration import (
    CALIBRATION_RELPATH,
    CalibrationSample,
    CalibrationStore,
    DEFAULT_ENVELOPES,
    Envelope,
    MIN_SAMPLES_FOR_CALIBRATION,
    current_envelopes,
    record_completion_sample,
)
from jig.ticket import Size, Ticket, WorkType


# ---- fixtures -----------------------------------------------------------


@pytest.fixture
async def analytics(tmp_path: Path) -> AnalyticsStore:
    store = AnalyticsStore(tmp_path / "analytics.jsonl")
    await store.load()
    return store


def _make_ticket(
    ticket_id: str = "t-1",
    size: Size = Size.M,
    dev_tier: str = "standard",
    layer: str | None = None,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        size=size,
        title="t",
        created_by="test",
        dev_tier=dev_tier,
        layer=layer,
    )


# ---- store --------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_round_trip(tmp_path: Path):
    store = CalibrationStore(tmp_path)
    await store.load()
    assert store.all() == []
    sample = CalibrationSample(
        ticket_id="t-1",
        size="m",
        observed_turns=12,
        observed_tool_calls=12,
        observed_duration_ms=300_000,
        observed_cost_usd=1.5,
    )
    await store.append(sample)

    # Path is canonical
    assert store.path == tmp_path / CALIBRATION_RELPATH
    assert store.path.is_file()

    # Round-trip via fresh load
    store2 = CalibrationStore(tmp_path)
    await store2.load()
    assert len(store2.all()) == 1
    assert store2.all()[0].ticket_id == "t-1"


# ---- record_completion_sample -------------------------------------------


@pytest.mark.asyncio
async def test_record_extracts_tool_calls_from_analytics(
    tmp_path: Path, analytics: AnalyticsStore
):
    """observed_tool_calls comes from ToolCalled events for the ticket's agent."""
    # Pre-seed AgentSpawned + tool calls.
    await analytics.append(
        AgentSpawned(
            agent_id="dev:t-1",
            role="dev",
            model="claude",
            ticket_id="t-1",
            spawned_by="orchestrator",
        )
    )
    for digest in ("a", "b", "c"):
        await analytics.append(
            ToolCalled(
                agent_id="dev:t-1",
                tool_name="Read",
                args_digest=digest,
                args_size_bytes=10,
                duration_ms=20,
                status="success",
            )
        )

    store = CalibrationStore(tmp_path)
    await store.load()
    ticket = _make_ticket("t-1", size=Size.S)
    sample = await record_completion_sample(
        ticket=ticket,
        status="success",
        duration_ms=120_000,
        cost_usd=0.5,
        tokens_in=1000,
        tokens_out=200,
        store=store,
        analytics=analytics,
    )
    assert sample.observed_tool_calls == 3
    assert sample.observed_turns == 3
    assert sample.observed_duration_ms == 120_000
    assert sample.observed_cost_usd == 0.5
    assert sample.size == "s"


# ---- envelope computation -----------------------------------------------


def test_envelopes_below_min_samples_fall_back_to_defaults(tmp_path: Path):
    store = CalibrationStore(tmp_path)
    # Fewer than MIN_SAMPLES_FOR_CALIBRATION samples for any size:
    # current_envelopes returns all defaults.
    env = current_envelopes(store)
    for size, default in DEFAULT_ENVELOPES.items():
        assert env[size] == default


@pytest.mark.asyncio
async def test_envelopes_compute_above_min_samples(tmp_path: Path):
    store = CalibrationStore(tmp_path)
    await store.load()
    # 6 successful S samples — turns 5,10,15,20,25,30
    for i, turns in enumerate((5, 10, 15, 20, 25, 30)):
        await store.append(
            CalibrationSample(
                ticket_id=f"t-s-{i}",
                size="s",
                observed_turns=turns,
                observed_tool_calls=turns,
                observed_duration_ms=turns * 1000,
                observed_cost_usd=turns * 0.05,
                completion_status="success",
            )
        )

    env = current_envelopes(store)
    s_env = env["s"]
    assert s_env.sample_count == 6
    # Median of 6 sorted values: average of (15, 20) = 17.5
    assert s_env.median_turns == 17.5
    # P90 of 6 values via linear interp; rank = 0.9 * 5 = 4.5 → 25 +
    # 0.5*(30-25) = 27.5
    assert s_env.p90_turns == 27.5


@pytest.mark.asyncio
async def test_envelopes_skip_failed_samples(tmp_path: Path):
    """Failed samples skew toward the long tail; calibration ignores them."""
    store = CalibrationStore(tmp_path)
    await store.load()
    # 5 successful S samples + 5 failed (which should be ignored).
    for i in range(5):
        await store.append(
            CalibrationSample(
                ticket_id=f"ok-{i}",
                size="s",
                observed_turns=10,
                observed_tool_calls=10,
                observed_duration_ms=60_000,
                observed_cost_usd=0.5,
                completion_status="success",
            )
        )
    for i in range(5):
        await store.append(
            CalibrationSample(
                ticket_id=f"fail-{i}",
                size="s",
                observed_turns=999,
                observed_tool_calls=999,
                observed_duration_ms=999_000,
                observed_cost_usd=99.0,
                completion_status="failed",
            )
        )
    env = current_envelopes(store)
    assert env["s"].median_turns == 10.0


# ---- threshold-shift event emission -------------------------------------


@pytest.mark.asyncio
async def test_threshold_shift_emits_calibration_updated(
    tmp_path: Path, analytics: AnalyticsStore
):
    """First crossover into calibrated envelope should fire an event."""
    store = CalibrationStore(tmp_path)
    await store.load()
    emitter = EventEmitter(analytics)

    # Pre-load 4 samples — below the calibration min so prior_envelopes
    # returns the default (median_turns=25). The fifth sample crosses
    # into the calibrated envelope (median=20), a 20% drop equal to
    # the shift threshold.
    for i in range(4):
        await store.append(
            CalibrationSample(
                ticket_id=f"warmup-{i}",
                size="m",
                observed_turns=20,
                observed_tool_calls=20,
                observed_duration_ms=240_000,
                observed_cost_usd=1.0,
                completion_status="success",
            )
        )

    # The fifth sample lands the calibrated envelope. Default median
    # was 25.0; new median is 20.0 — a 20% shift, equal to the
    # ENVELOPE_SHIFT_THRESHOLD, so the event fires.
    ticket = _make_ticket("trigger", size=Size.M)
    await record_completion_sample(
        ticket=ticket,
        status="success",
        duration_ms=240_000,
        cost_usd=1.0,
        tokens_in=None,
        tokens_out=None,
        store=store,
        analytics=analytics,
        emitter=emitter,
    )
    await emitter.drain()
    events = await analytics.by_kind("estimation_calibration_updated")
    assert len(events) >= 1
    ev = events[-1]
    assert isinstance(ev, EstimationCalibrationUpdated)
    # Bands payload includes the M size we just calibrated.
    assert "M" in ev.bands["standard"]


# ---- auto-escalation calibration integration ----------------------------


@pytest.mark.asyncio
async def test_out_of_budget_signal_uses_calibrated_envelope(
    tmp_path: Path, analytics: AnalyticsStore
):
    """When calibrated p90 is small, agent burning many turns trips out_of_budget."""
    store = CalibrationStore(tmp_path)
    await store.load()
    # Calibrated S envelope with p90=10 turns.
    for i in range(6):
        await store.append(
            CalibrationSample(
                ticket_id=f"warmup-{i}",
                size="s",
                observed_turns=10,
                observed_tool_calls=10,
                observed_duration_ms=120_000,
                observed_cost_usd=0.5,
                completion_status="success",
            )
        )

    # Now seed agent activity for the test ticket: AgentSpawned + 25 tool
    # calls (well above p90=10 * 1.85 over-budget cap).
    await analytics.append(
        AgentSpawned(
            agent_id="dev:hot-1",
            role="dev",
            model="claude",
            ticket_id="hot-1",
            spawned_by="orchestrator",
        )
    )
    for i in range(25):
        await analytics.append(
            ToolCalled(
                agent_id="dev:hot-1",
                tool_name="Read",
                args_digest=f"d-{i}",  # distinct so flailing doesn't trip
                args_size_bytes=5,
                duration_ms=10,
                status="success",
            )
        )

    signals = await check_escalation_signals(
        "hot-1",
        analytics,
        config=AutoEscalationConfig(),
        calibration_store=store,
        ticket_size="s",
    )
    kinds = [s.kind for s in signals]
    assert "out_of_budget" in kinds


@pytest.mark.asyncio
async def test_out_of_budget_skipped_without_calibration(
    tmp_path: Path, analytics: AnalyticsStore
):
    """No calibration_store argument → no out_of_budget signal (back-compat)."""
    await analytics.append(
        AgentSpawned(
            agent_id="dev:hot-1",
            role="dev",
            model="claude",
            ticket_id="hot-1",
            spawned_by="orchestrator",
        )
    )
    for i in range(25):
        await analytics.append(
            ToolCalled(
                agent_id="dev:hot-1",
                tool_name="Read",
                args_digest=f"d-{i}",
                args_size_bytes=5,
                duration_ms=10,
                status="success",
            )
        )

    signals = await check_escalation_signals("hot-1", analytics)
    kinds = [s.kind for s in signals]
    assert "out_of_budget" not in kinds


# ---- envelope shape -----------------------------------------------------


def test_default_envelopes_cover_every_size():
    expected = {Size.XS.value, Size.S.value, Size.M.value, Size.L.value, Size.XL.value}
    assert set(DEFAULT_ENVELOPES) == expected
    for env in DEFAULT_ENVELOPES.values():
        assert isinstance(env, Envelope)
        assert env.sample_count == 0


def test_min_samples_constant_is_at_least_one():
    """The constant gates first-sample-emit behavior — must be ≥ 1."""
    assert MIN_SAMPLES_FOR_CALIBRATION >= 1


# ---- CLI ---------------------------------------------------------------


def test_pm_calibration_show_cli_runs(tmp_path: Path):
    """jig pm calibration show prints JSON envelopes."""
    from click.testing import CliRunner

    from jig.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["pm", "calibration", "show", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0
    # Output is JSON; default envelopes carry sample_count=0
    assert '"sample_count": 0' in result.output
    assert '"m"' in result.output


def test_pm_calibration_show_cli_size_filter(tmp_path: Path):
    """--size m prints only the M envelope."""
    from click.testing import CliRunner

    from jig.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["pm", "calibration", "show", "--size", "m", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0
    assert '"size": "m"' in result.output
