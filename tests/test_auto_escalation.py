"""Auto-escalation threshold checker (Track F MVP).

Per ``docs/pm-workflow/design.md`` §"Auto-escalation thresholds": the
Coordinator monitors mechanical signals to force-escalate dev agents
that systematically underclaim being stuck. The checker is single-digit-
second latency (no LLM) and runs over the existing analytics events.

Bones scope: each threshold's trip condition + the no-signal happy
path. Wiring into the orchestrator's per-ticket lifecycle is a separate
hook task.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import (
    AgentSpawned,
    AutoEscalationTriggered,
    PerCommitCheckFailed,
    ToolCalled,
)
from jig.analytics.store import AnalyticsStore
from jig.auto_escalation import (
    AutoEscalationConfig,
    EscalationSignal,
    check_escalation_signals,
    emit_signals_as_events,
)


@pytest.fixture
async def analytics(tmp_path: Path) -> AnalyticsStore:
    store = AnalyticsStore(tmp_path / "analytics.jsonl")
    await store.load()
    return store


# ---- defaults -----------------------------------------------------------


def test_config_defaults_match_design():
    """Conservative defaults straight from design doc."""
    cfg = AutoEscalationConfig()
    assert cfg.repeated_same_failure == 3
    assert cfg.tool_call_flailing == 12
    assert cfg.no_commit_drift_minutes == 30
    assert cfg.out_of_budget_pct == 0.85
    assert cfg.forced_reflection_at_minutes == 20


# ---- no signals ---------------------------------------------------------


@pytest.mark.asyncio
async def test_no_signals_when_store_empty(analytics):
    signals = await check_escalation_signals("tb-cat", analytics)
    assert signals == []


@pytest.mark.asyncio
async def test_no_signals_for_well_behaved_ticket(analytics):
    """One agent spawn, a couple of successful tool calls — nothing trips."""
    await analytics.append(
        AgentSpawned(
            agent_id="dev-1",
            role="dev",
            tier="standard",
            model="claude",
            ticket_id="tb-cat",
            spawned_by="orchestrator",
        )
    )
    for _ in range(2):
        await analytics.append(
            ToolCalled(
                agent_id="dev-1",
                tool_name="Bash",
                args_digest="d",
                args_size_bytes=10,
                duration_ms=100,
                status="success",
            )
        )
    signals = await check_escalation_signals("tb-cat", analytics)
    assert signals == []


# ---- repeated_same_failure ---------------------------------------------


@pytest.mark.asyncio
async def test_repeated_same_failure_trips(analytics):
    """3 PerCommitCheckFailed events on the same contract URI trip the threshold."""
    for _ in range(3):
        await analytics.append(
            PerCommitCheckFailed(
                ticket_id="tb-cat",
                agent_id="dev-1",
                commit_sha="abc",
                reviewer_role="contract_compliance",
                violation_category="ownership",
                contract_uri="contract://catalog-ingest/products",
                severity="critical",
                auto_applied=False,
            )
        )
    signals = await check_escalation_signals("tb-cat", analytics)
    kinds = {s.kind for s in signals}
    assert "repeated_same_failure" in kinds


@pytest.mark.asyncio
async def test_repeated_same_failure_doesnt_trip_under_threshold(analytics):
    for _ in range(2):
        await analytics.append(
            PerCommitCheckFailed(
                ticket_id="tb-cat",
                agent_id="dev-1",
                commit_sha="abc",
                reviewer_role="contract_compliance",
                violation_category="ownership",
                contract_uri="contract://catalog-ingest/products",
                severity="critical",
                auto_applied=False,
            )
        )
    signals = await check_escalation_signals("tb-cat", analytics)
    assert all(s.kind != "repeated_same_failure" for s in signals)


@pytest.mark.asyncio
async def test_repeated_same_failure_only_for_target_ticket(analytics):
    """Failures on a different ticket don't trip this ticket's threshold."""
    for _ in range(3):
        await analytics.append(
            PerCommitCheckFailed(
                ticket_id="tb-other",
                agent_id="dev-2",
                commit_sha="xyz",
                reviewer_role="contract_compliance",
                violation_category="ownership",
                contract_uri="contract://catalog-ingest/products",
                severity="critical",
                auto_applied=False,
            )
        )
    signals = await check_escalation_signals("tb-cat", analytics)
    assert all(s.kind != "repeated_same_failure" for s in signals)


# ---- tool_call_flailing -------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_flailing_trips(analytics):
    """``tool_call_flailing`` count of repeated identical calls trips."""
    await analytics.append(
        AgentSpawned(
            agent_id="dev-1",
            role="dev",
            tier="standard",
            model="claude",
            ticket_id="tb-cat",
            spawned_by="orchestrator",
        )
    )
    cfg = AutoEscalationConfig()
    for _ in range(cfg.tool_call_flailing):
        await analytics.append(
            ToolCalled(
                agent_id="dev-1",
                tool_name="Bash",
                args_digest="same-digest",
                args_size_bytes=10,
                duration_ms=10,
                status="error",
            )
        )
    signals = await check_escalation_signals("tb-cat", analytics)
    assert any(s.kind == "tool_call_flailing" for s in signals)


@pytest.mark.asyncio
async def test_tool_call_flailing_doesnt_trip_for_diverse_calls(analytics):
    await analytics.append(
        AgentSpawned(
            agent_id="dev-1",
            role="dev",
            tier="standard",
            model="claude",
            ticket_id="tb-cat",
            spawned_by="orchestrator",
        )
    )
    for i in range(20):
        await analytics.append(
            ToolCalled(
                agent_id="dev-1",
                tool_name="Bash",
                args_digest=f"digest-{i}",  # all different
                args_size_bytes=10,
                duration_ms=10,
                status="success",
            )
        )
    signals = await check_escalation_signals("tb-cat", analytics)
    assert all(s.kind != "tool_call_flailing" for s in signals)


# ---- no_commit_drift ----------------------------------------------------


@pytest.mark.asyncio
async def test_no_commit_drift_trips_when_agent_idle(analytics):
    """Agent spawned long ago, never committed — trips drift."""
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=45)
    await analytics.append(
        AgentSpawned(
            agent_id="dev-1",
            role="dev",
            tier="standard",
            model="claude",
            ticket_id="tb-cat",
            spawned_by="orchestrator",
            timestamp=long_ago,
        )
    )
    # Some tool activity to establish "active but not progressing".
    await analytics.append(
        ToolCalled(
            agent_id="dev-1",
            tool_name="Read",
            args_digest="d",
            args_size_bytes=10,
            duration_ms=10,
            status="success",
        )
    )
    signals = await check_escalation_signals("tb-cat", analytics)
    assert any(s.kind == "no_commit_drift" for s in signals)


@pytest.mark.asyncio
async def test_no_commit_drift_doesnt_trip_for_recent_spawn(analytics):
    await analytics.append(
        AgentSpawned(
            agent_id="dev-1",
            role="dev",
            tier="standard",
            model="claude",
            ticket_id="tb-cat",
            spawned_by="orchestrator",
        )
    )
    signals = await check_escalation_signals("tb-cat", analytics)
    assert all(s.kind != "no_commit_drift" for s in signals)


# ---- forced_reflection_at_minutes ---------------------------------------


@pytest.mark.asyncio
async def test_forced_reflection_at_minutes_trips_after_threshold(analytics):
    """Agent active longer than the reflection window — trips reflection."""
    long_ago = datetime.now(timezone.utc) - timedelta(minutes=25)
    await analytics.append(
        AgentSpawned(
            agent_id="dev-1",
            role="dev",
            tier="standard",
            model="claude",
            ticket_id="tb-cat",
            spawned_by="orchestrator",
            timestamp=long_ago,
        )
    )
    signals = await check_escalation_signals("tb-cat", analytics)
    assert any(s.kind == "forced_reflection_at_minutes" for s in signals)


@pytest.mark.asyncio
async def test_forced_reflection_doesnt_trip_for_short_runs(analytics):
    await analytics.append(
        AgentSpawned(
            agent_id="dev-1",
            role="dev",
            tier="standard",
            model="claude",
            ticket_id="tb-cat",
            spawned_by="orchestrator",
        )
    )
    signals = await check_escalation_signals("tb-cat", analytics)
    assert all(s.kind != "forced_reflection_at_minutes" for s in signals)


# ---- emit_signals_as_events --------------------------------------------


@pytest.mark.asyncio
async def test_emit_signals_writes_auto_escalation_event(analytics, tmp_path):
    """When a signal fires, an AutoEscalationTriggered event lands in the store."""
    emitter = EventEmitter(analytics, simulator_mode=True)
    signal = EscalationSignal(
        kind="repeated_same_failure",
        threshold_value=3,
        observed_value=3,
        detail="3 contract failures on contract://x",
    )
    emit_signals_as_events(
        emitter,
        signals=[signal],
        ticket_id="tb-cat",
        agent_id="dev-1",
        from_tier="standard",
    )
    await emitter.drain()
    events = await analytics.by_kind("auto_escalation_triggered")
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, AutoEscalationTriggered)
    assert ev.ticket_id == "tb-cat"
    assert ev.trip_signal == "repeated_same_failure"


@pytest.mark.asyncio
async def test_emit_no_signals_writes_nothing(analytics):
    emitter = EventEmitter(analytics, simulator_mode=True)
    emit_signals_as_events(
        emitter, signals=[], ticket_id="tb-cat", agent_id="dev-1",
        from_tier="standard",
    )
    await emitter.drain()
    events = await analytics.by_kind("auto_escalation_triggered")
    assert events == []
