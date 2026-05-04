"""Quartermaster — on-demand operator briefing (Track I MVP).

Per ``docs/agent-leverage/problem.md`` §3: continuous background agent
reading the analytics event stream. MVP scope: deterministic
aggregation only (no LLM); operator invokes on demand. Three
deliverables tested here: aggregation, markdown formatter, MCP handler.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jig.analytics.events import (
    AgentCompleted,
    AutoEscalationTriggered,
    EscalationRouted,
    ReviewCommentPosted,
    TicketStateChanged,
)
from jig.analytics.store import AnalyticsStore
from jig.persistence import load_role
from jig.quartermaster import (
    DEFAULT_WINDOW_DAYS,
    HeadlineMetrics,
    Pattern,
    Quartermaster,
    WeeklyBriefing,
    format_briefing,
)


# ---- helpers -------------------------------------------------------------


def _at(offset_hours: float, base: datetime | None = None) -> datetime:
    """Reference timestamp inside the briefing window."""
    base = base or datetime.now(timezone.utc)
    return base - timedelta(hours=offset_hours)


async def _store(path: Path) -> AnalyticsStore:
    s = AnalyticsStore(path / "events.jsonl")
    await s.load()
    return s


# ---- aggregation ---------------------------------------------------------


@pytest.mark.asyncio
async def test_briefing_window_defaults_to_seven_days(tmp_path: Path):
    """No window argument → last 7 days; period_end is "now"."""
    s = await _store(tmp_path)
    qm = Quartermaster(s)
    briefing = await qm.briefing()
    delta = briefing.period_end - briefing.period_start
    assert abs(delta.total_seconds() - DEFAULT_WINDOW_DAYS * 86400) < 1


@pytest.mark.asyncio
async def test_briefing_excludes_events_outside_window(tmp_path: Path):
    """Events older than the window are filtered out of every aggregation."""
    s = await _store(tmp_path)
    # Inside-window: a completed and a failed ticket
    await s.append(
        TicketStateChanged(timestamp=_at(2), ticket_id="t-1", to_state="resolved")
    )
    await s.append(
        TicketStateChanged(timestamp=_at(2), ticket_id="t-2", to_state="failed")
    )
    # Outside-window: 30 days back (should be ignored)
    await s.append(
        TicketStateChanged(
            timestamp=_at(24 * 30), ticket_id="t-3", to_state="resolved"
        )
    )
    qm = Quartermaster(s)
    b = await qm.briefing()
    assert b.headline_metrics.tickets_completed == 1
    assert b.headline_metrics.tickets_failed == 1


@pytest.mark.asyncio
async def test_headline_metrics_count_state_transitions(tmp_path: Path):
    s = await _store(tmp_path)
    for i in range(3):
        await s.append(
            TicketStateChanged(
                timestamp=_at(1), ticket_id=f"t-{i}", to_state="resolved"
            )
        )
    await s.append(
        TicketStateChanged(timestamp=_at(1), ticket_id="t-fail", to_state="failed")
    )
    await s.append(
        TicketStateChanged(
            timestamp=_at(1), ticket_id="t-prog", to_state="in_progress"
        )
    )
    qm = Quartermaster(s)
    b = await qm.briefing()
    assert b.headline_metrics.tickets_completed == 3
    assert b.headline_metrics.tickets_failed == 1
    assert b.headline_metrics.tickets_in_progress == 1


@pytest.mark.asyncio
async def test_headline_metrics_count_escalations(tmp_path: Path):
    s = await _store(tmp_path)
    # Both EscalationRouted and AutoEscalationTriggered count toward
    # the headline; the operator cares about the rate, not the source.
    await s.append(
        EscalationRouted(
            timestamp=_at(1),
            from_agent="dev-1",
            to_target="sa",
            reason_category="contract_gap",
            ticket_id="t-1",
        )
    )
    await s.append(
        AutoEscalationTriggered(
            timestamp=_at(1),
            ticket_id="t-2",
            agent_id="dev-2",
            from_tier="standard",
            to_tier="senior",
            trip_signal="repeated_same_failure",
            turns_at_trip=12,
        )
    )
    qm = Quartermaster(s)
    b = await qm.briefing()
    assert b.headline_metrics.escalations == 2


@pytest.mark.asyncio
async def test_avg_cycle_time_from_agent_completions(tmp_path: Path):
    """``avg_cycle_time_seconds`` averages ``AgentCompleted.duration_ms``
    across successful agent runs in-window. Failed/blocked runs are
    excluded — they're noise on the cycle-time signal.
    """
    s = await _store(tmp_path)
    await s.append(
        AgentCompleted(
            timestamp=_at(1), agent_id="a-1", status="success", duration_ms=120_000
        )
    )
    await s.append(
        AgentCompleted(
            timestamp=_at(1), agent_id="a-2", status="success", duration_ms=180_000
        )
    )
    await s.append(
        AgentCompleted(
            timestamp=_at(1), agent_id="a-3", status="failed", duration_ms=30_000
        )
    )
    qm = Quartermaster(s)
    b = await qm.briefing()
    assert b.headline_metrics.avg_cycle_time_seconds == pytest.approx(150.0)


@pytest.mark.asyncio
async def test_avg_cycle_time_none_when_no_successful_runs(tmp_path: Path):
    s = await _store(tmp_path)
    qm = Quartermaster(s)
    b = await qm.briefing()
    assert b.headline_metrics.avg_cycle_time_seconds is None


# ---- patterns ------------------------------------------------------------


@pytest.mark.asyncio
async def test_pattern_module_with_repeated_escalations(tmp_path: Path):
    """One module accumulating > N escalations on its tickets surfaces.

    MVP: deterministic threshold (>=3 escalations within window on the
    same module). The synthetic event stream carries ticket_id only;
    the quartermaster looks up module via the TicketStore (passed in
    at construction).
    """
    s = await _store(tmp_path)
    # Three escalations on tickets that all live in module "auth".
    for i in range(3):
        await s.append(
            EscalationRouted(
                timestamp=_at(1),
                from_agent=f"dev-{i}",
                to_target="sa",
                reason_category="contract_gap",
                ticket_id=f"t-auth-{i}",
            )
        )
    # Module map provided by the caller — keeps the quartermaster
    # independent of TicketStore here so tests can pass synthetic
    # data without bootstrapping a full project.
    ticket_to_module = {f"t-auth-{i}": "auth" for i in range(3)}

    qm = Quartermaster(s, ticket_to_module=ticket_to_module)
    b = await qm.briefing()
    kinds = {p.kind for p in b.notable_patterns}
    assert "module_repeated_escalations" in kinds
    p = next(p for p in b.notable_patterns if p.kind == "module_repeated_escalations")
    assert "auth" in p.description
    assert len(p.evidence_event_ids) == 3


@pytest.mark.asyncio
async def test_pattern_reviewer_repeating_same_comment_type(tmp_path: Path):
    s = await _store(tmp_path)
    # Same reviewer (intent-compliance) firing the same comment type
    # repeatedly is a signal — operator cares about which check is
    # consistently catching things.
    for i in range(4):
        await s.append(
            ReviewCommentPosted(
                timestamp=_at(1),
                reviewer_id="intent-compliance",
                reviewer_role="intent_compliance",
                ticket_id=f"t-{i}",
                comment_id=f"c-{i}",
                comment_type="intent-too-short",
                severity="important",
                confidence=1.0,
            )
        )
    qm = Quartermaster(s)
    b = await qm.briefing()
    kinds = {p.kind for p in b.notable_patterns}
    assert "reviewer_repeating_comment_type" in kinds


@pytest.mark.asyncio
async def test_pattern_stalled_tickets(tmp_path: Path):
    """Tickets in_progress for > N days surface as stalled.

    Window-relative: a ticket whose most recent in_progress transition
    happened > stall_threshold_days ago AND has no terminal state yet.
    """
    s = await _store(tmp_path)
    # In-window in_progress, but nothing further → potentially stalled.
    # Use a long-stalled ticket: in_progress 4 days ago, never resolved.
    await s.append(
        TicketStateChanged(
            timestamp=_at(24 * 4),
            ticket_id="t-stalled",
            from_state="open",
            to_state="in_progress",
        )
    )
    # Healthy ticket: in_progress 1h ago.
    await s.append(
        TicketStateChanged(
            timestamp=_at(1),
            ticket_id="t-fresh",
            from_state="open",
            to_state="in_progress",
        )
    )
    qm = Quartermaster(s, stall_threshold_days=2)
    b = await qm.briefing()
    stalled = [p for p in b.notable_patterns if p.kind == "tickets_stalled"]
    assert len(stalled) == 1
    assert "t-stalled" in stalled[0].description
    assert "t-fresh" not in stalled[0].description


@pytest.mark.asyncio
async def test_no_patterns_when_event_stream_quiet(tmp_path: Path):
    s = await _store(tmp_path)
    qm = Quartermaster(s)
    b = await qm.briefing()
    assert b.notable_patterns == []
    assert b.attention_recommendations == []


# ---- attention recommendations -------------------------------------------


@pytest.mark.asyncio
async def test_attention_recommendations_top_three(tmp_path: Path):
    """The top-3 attention items are derived from notable_patterns,
    keyed by severity (escalations > stalls > reviewer-noise)."""
    s = await _store(tmp_path)
    # Generate enough patterns to exceed 3 items.
    for i in range(3):
        await s.append(
            EscalationRouted(
                timestamp=_at(1),
                from_agent=f"d-{i}",
                to_target="sa",
                reason_category="x",
                ticket_id=f"t-mod-a-{i}",
            )
        )
    for i in range(4):
        await s.append(
            ReviewCommentPosted(
                timestamp=_at(1),
                reviewer_id="intent-compliance",
                reviewer_role="intent_compliance",
                ticket_id=f"t-{i}",
                comment_id=f"c-{i}",
                comment_type="intent-too-short",
                severity="important",
                confidence=1.0,
            )
        )
    await s.append(
        TicketStateChanged(
            timestamp=_at(24 * 5),
            ticket_id="t-stall",
            from_state="open",
            to_state="in_progress",
        )
    )
    qm = Quartermaster(
        s,
        ticket_to_module={f"t-mod-a-{i}": "mod-a" for i in range(3)},
        stall_threshold_days=2,
    )
    b = await qm.briefing()
    assert len(b.attention_recommendations) <= 3
    # First recommendation should be the highest-severity pattern
    # (module repeated escalations beats reviewer noise).
    assert "module_repeated_escalations" in b.attention_recommendations[0]


# ---- formatter -----------------------------------------------------------


def test_format_briefing_renders_all_sections():
    """Markdown formatter exposes period, headline metrics, patterns,
    recommendations. Operator-facing — must be skim-able."""
    now = datetime.now(timezone.utc)
    b = WeeklyBriefing(
        period_start=now - timedelta(days=7),
        period_end=now,
        headline_metrics=HeadlineMetrics(
            tickets_completed=12,
            tickets_failed=2,
            tickets_in_progress=4,
            escalations=3,
            avg_cycle_time_seconds=420.0,
        ),
        notable_patterns=[
            Pattern(
                kind="module_repeated_escalations",
                description="Module 'auth' has 3 escalations this period.",
                evidence_event_ids=["e-1", "e-2", "e-3"],
            )
        ],
        attention_recommendations=["module_repeated_escalations: auth"],
    )
    md = format_briefing(b)
    assert "# Quartermaster Briefing" in md
    assert "Tickets completed" in md
    assert "12" in md
    assert "auth" in md
    assert "module_repeated_escalations" in md


def test_format_briefing_handles_empty_recommendations():
    now = datetime.now(timezone.utc)
    b = WeeklyBriefing(
        period_start=now - timedelta(days=7),
        period_end=now,
        headline_metrics=HeadlineMetrics(
            tickets_completed=0,
            tickets_failed=0,
            tickets_in_progress=0,
            escalations=0,
            avg_cycle_time_seconds=None,
        ),
        notable_patterns=[],
        attention_recommendations=[],
    )
    md = format_briefing(b)
    # Friendly empty-state copy so the operator doesn't think it crashed.
    assert "no notable patterns" in md.lower() or "nothing to report" in md.lower()


# ---- mcp handler ---------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_handler_returns_markdown(tmp_path: Path):
    """The MCP tool ``quartermaster_briefing`` runs the quartermaster
    against the project's analytics store and returns the markdown.
    """
    from jig.quartermaster import handle_quartermaster_briefing

    # Project layout: .jig/store/analytics.jsonl is where AnalyticsStore lives.
    project_path = tmp_path
    store_dir = project_path / ".jig" / "store"
    store_dir.mkdir(parents=True)
    s = AnalyticsStore(store_dir / "analytics.jsonl")
    await s.load()
    await s.append(
        TicketStateChanged(
            timestamp=_at(1), ticket_id="t-1", to_state="resolved"
        )
    )

    md = await handle_quartermaster_briefing(project_path=project_path)
    assert "# Quartermaster Briefing" in md
    assert "Tickets completed" in md


@pytest.mark.asyncio
async def test_mcp_handler_handles_missing_analytics_file(tmp_path: Path):
    """No events.jsonl yet → returns a briefing with zero counts, not crash."""
    from jig.quartermaster import handle_quartermaster_briefing

    md = await handle_quartermaster_briefing(project_path=tmp_path)
    assert "# Quartermaster Briefing" in md


# ---- role config ---------------------------------------------------------


def test_quartermaster_role_config_loads(tmp_path: Path):
    cfg = load_role(tmp_path, "quartermaster")
    assert cfg.role == "quartermaster"
    assert cfg.strict_tools is True
    assert "quartermaster_briefing" in cfg.allowed_tools
    assert "Read" in cfg.allowed_tools
    # Must NOT have authoring tools — quartermaster is read-only by design.
    for forbidden in (
        "Write",
        "Edit",
        "Bash",
        "sa_finalize",
        "plan_finalize",
        "l0_finalize",
        "l3_finalize",
        "spec_publish",
        "create_ticket",
        "update_ticket",
    ):
        assert forbidden not in cfg.allowed_tools


# ---- mcp registration ----------------------------------------------------


@pytest.mark.asyncio
async def test_quartermaster_briefing_registered_when_allowed(tmp_path: Path, monkeypatch):
    """The MCP server exposes ``quartermaster_briefing`` when allowed."""
    import jig.mcp_server as mcp_server_mod
    from jig.mcp_server import create_agent_mcp_server
    from jig.models import RoleConfig
    from jig.store.bus import MessageBus
    from jig.store.memory import MemoryStore
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore

    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()

    cfg = RoleConfig(
        role="quartermaster",
        allowed_tools=["Read", "quartermaster_briefing"],
        strict_tools=True,
    )
    captured: dict = {}
    real = mcp_server_mod.create_sdk_mcp_server

    def spy(*, name, tools):
        captured["tools"] = tools
        return real(name=name, tools=tools)

    monkeypatch.setattr(mcp_server_mod, "create_sdk_mcp_server", spy)

    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="quartermaster",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = {t.name for t in captured["tools"]}
    assert "quartermaster_briefing" in names


@pytest.mark.asyncio
async def test_quartermaster_briefing_not_registered_when_not_allowed(tmp_path: Path, monkeypatch):
    import jig.mcp_server as mcp_server_mod
    from jig.mcp_server import create_agent_mcp_server
    from jig.models import RoleConfig
    from jig.store.bus import MessageBus
    from jig.store.memory import MemoryStore
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore

    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()

    cfg = RoleConfig(
        role="dev",
        allowed_tools=["Read", "Bash"],
        strict_tools=True,
    )
    captured: dict = {}
    real = mcp_server_mod.create_sdk_mcp_server

    def spy(*, name, tools):
        captured["tools"] = tools
        return real(name=name, tools=tools)

    monkeypatch.setattr(mcp_server_mod, "create_sdk_mcp_server", spy)

    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="dev",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = {t.name for t in captured["tools"]}
    assert "quartermaster_briefing" not in names


# ---- Final scope: feedback loop + calibration ----------------------------


@pytest.mark.asyncio
async def test_briefing_carries_deterministic_id(tmp_path: Path):
    """Every briefing carries an id derived from period_end so feedback
    can reference the briefing the operator just saw."""
    s = await _store(tmp_path)
    qm = Quartermaster(s)
    fixed_end = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
    b = await qm.briefing(period_end=fixed_end)
    assert b.id == "brief-20260501T120000Z"


@pytest.mark.asyncio
async def test_record_feedback_round_trip(tmp_path: Path):
    """Recording a feedback row writes it to disk; loading round-trips."""
    from jig.quartermaster import (
        BriefingFeedback,
        load_feedback,
        record_feedback,
    )

    row_id = await record_feedback(
        tmp_path,
        briefing_id="brief-test",
        useful=False,
        not_useful_pattern_ids=["module_repeated_escalations"],
        note="too noisy on the auth module",
    )
    assert row_id
    rows = await load_feedback(tmp_path)
    assert len(rows) == 1
    assert isinstance(rows[0], BriefingFeedback)
    assert rows[0].briefing_id == "brief-test"
    assert rows[0].useful is False
    assert rows[0].not_useful_pattern_ids == ["module_repeated_escalations"]
    assert rows[0].note == "too noisy on the auth module"


@pytest.mark.asyncio
async def test_record_feedback_rejects_unknown_pattern_id(tmp_path: Path):
    """Typo'd pattern ids raise ValueError at record time so they don't
    silently produce un-tunable rows."""
    from jig.quartermaster import record_feedback

    with pytest.raises(ValueError, match="unknown pattern id"):
        await record_feedback(
            tmp_path,
            briefing_id="brief-test",
            useful=False,
            not_useful_pattern_ids=["totally_made_up_pattern"],
        )


@pytest.mark.asyncio
async def test_calibration_raises_threshold_on_not_useful(tmp_path: Path):
    """Each not_useful_pattern_ids hit raises that pattern's threshold by 1."""
    from jig.quartermaster import (
        compute_calibration,
        load_feedback,
        record_feedback,
    )

    await record_feedback(
        tmp_path,
        briefing_id="b1",
        useful=False,
        not_useful_pattern_ids=["module_repeated_escalations"],
    )
    cal = compute_calibration(await load_feedback(tmp_path))
    # Default is 3; one not-useful bumps it to 4.
    assert cal.threshold_for("module_repeated_escalations") == 4


@pytest.mark.asyncio
async def test_calibration_useful_does_not_change_thresholds(tmp_path: Path):
    """``useful=True`` rows do not touch any calibration."""
    from jig.quartermaster import (
        compute_calibration,
        load_feedback,
        record_feedback,
    )

    await record_feedback(
        tmp_path,
        briefing_id="b1",
        useful=True,
    )
    cal = compute_calibration(await load_feedback(tmp_path))
    # No entry → falls back to default.
    assert cal.threshold_for("module_repeated_escalations") == 3
    assert cal.threshold_for("reviewer_repeating_comment_type") == 3
    assert cal.threshold_for("tickets_stalled") == 3


@pytest.mark.asyncio
async def test_calibration_caps_at_2x_default(tmp_path: Path):
    """Cap prevents runaway calibration from disabling a pattern entirely."""
    from jig.quartermaster import (
        compute_calibration,
        load_feedback,
        record_feedback,
    )

    for i in range(10):
        await record_feedback(
            tmp_path,
            briefing_id=f"b{i}",
            useful=False,
            not_useful_pattern_ids=["module_repeated_escalations"],
        )
    cal = compute_calibration(await load_feedback(tmp_path))
    # Default 3 × 2 = 6 cap.
    assert cal.threshold_for("module_repeated_escalations") == 6


def test_calibration_drifts_back_after_30_days():
    """30 days since the last not-useful feedback drifts threshold one
    step back toward the default."""
    from jig.quartermaster import BriefingFeedback, compute_calibration

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fb = [
        BriefingFeedback(
            briefing_id="b1",
            useful=False,
            not_useful_pattern_ids=["tickets_stalled"],
            timestamp=base,
        ),
    ]
    # Same day → still elevated (default 3 → 4).
    cal_now = compute_calibration(fb, now=base)
    assert cal_now.threshold_for("tickets_stalled") == 4
    # 31 days later → drifts back to default.
    drifted = compute_calibration(fb, now=base + timedelta(days=31))
    assert drifted.threshold_for("tickets_stalled") == 3


def test_calibration_drift_floors_at_default():
    """Drift never pushes thresholds below the default (silently
    disabling the pattern would be worse than restoring baseline)."""
    from jig.quartermaster import BriefingFeedback, compute_calibration

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fb = [
        BriefingFeedback(
            briefing_id="b1",
            useful=False,
            not_useful_pattern_ids=["tickets_stalled"],
            timestamp=base,
        ),
    ]
    # 365 days later → bottoms out at default (no negatives).
    drifted = compute_calibration(fb, now=base + timedelta(days=365))
    assert drifted.threshold_for("tickets_stalled") == 3


@pytest.mark.asyncio
async def test_quartermaster_uses_calibration_for_thresholds(tmp_path: Path):
    """When a calibration is supplied to the constructor, the thresholds
    come from it rather than from the constructor defaults."""
    from jig.quartermaster import PatternCalibration, Quartermaster

    s = await _store(tmp_path)
    # Author 4 escalations on one module — would exceed default 3 but
    # not a calibrated 5.
    for i in range(4):
        await s.append(
            EscalationRouted(
                timestamp=_at(1),
                from_agent=f"d-{i}",
                to_target="sa",
                reason_category="x",
                ticket_id=f"t-mod-a-{i}",
            )
        )
    cal = PatternCalibration(thresholds={"module_repeated_escalations": 5})
    qm = Quartermaster(
        s,
        ticket_to_module={f"t-mod-a-{i}": "mod-a" for i in range(4)},
        calibration=cal,
    )
    b = await qm.briefing()
    kinds = {p.kind for p in b.notable_patterns}
    # Calibrated threshold of 5 means 4 escalations no longer trigger.
    assert "module_repeated_escalations" not in kinds


@pytest.mark.asyncio
async def test_get_pattern_calibration_round_trip(tmp_path: Path):
    """``get_pattern_calibration`` returns the live calibration off disk."""
    from jig.quartermaster import (
        get_pattern_calibration,
        record_feedback,
    )

    await record_feedback(
        tmp_path,
        briefing_id="b1",
        useful=False,
        not_useful_pattern_ids=["reviewer_repeating_comment_type"],
    )
    cal = await get_pattern_calibration(tmp_path)
    assert cal.threshold_for("reviewer_repeating_comment_type") == 4


@pytest.mark.asyncio
async def test_handle_quartermaster_briefing_applies_live_calibration(tmp_path: Path):
    """End-to-end: feedback → calibration → next briefing reflects the
    raised threshold without a Quartermaster being constructed by the
    test."""
    from jig.quartermaster import (
        handle_quartermaster_briefing,
        record_feedback,
    )
    from jig.store.tickets import TicketStore
    from jig.ticket import Ticket, WorkType

    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True)
    s = AnalyticsStore(store_dir / "analytics.jsonl")
    await s.load()
    # Seed 4 tickets all on module ``mod-a`` so the escalations attribute.
    tickets = TicketStore(store_dir / "tickets.jsonl")
    await tickets.load()
    for i in range(4):
        await tickets.create(
            Ticket(
                id=f"t-mod-a-{i}",
                work_type=WorkType.FEATURE,
                title=f"t{i}",
                created_by="planner-pm",
                module_id="mod-a",
            )
        )
    # 4 escalations on tickets in the same module — exceeds default 3.
    for i in range(4):
        await s.append(
            EscalationRouted(
                timestamp=_at(1),
                from_agent=f"d-{i}",
                to_target="sa",
                reason_category="x",
                ticket_id=f"t-mod-a-{i}",
            )
        )
    # First briefing: pattern fires.
    md_before = await handle_quartermaster_briefing(project_path=tmp_path)
    assert "module_repeated_escalations" in md_before

    # Operator marks the pattern noisy twice — bumps threshold from 3 to 5.
    for i in range(2):
        await record_feedback(
            tmp_path,
            briefing_id=f"prior-{i}",
            useful=False,
            not_useful_pattern_ids=["module_repeated_escalations"],
        )

    md_after = await handle_quartermaster_briefing(project_path=tmp_path)
    # 4 escalations no longer clear the calibrated threshold of 5.
    assert "module_repeated_escalations" not in md_after


# ---- CLI -----------------------------------------------------------------


def test_cli_quartermaster_feedback_records_row(tmp_path: Path):
    """``jig quartermaster feedback`` writes a row that round-trips."""
    import asyncio as _asyncio

    from click.testing import CliRunner

    from jig.cli import cli
    from jig.quartermaster import load_feedback

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "quartermaster",
            "feedback",
            "brief-20260501T120000Z",
            "--not-useful",
            "--noisy-pattern",
            "tickets_stalled",
            "--note",
            "stall threshold too tight on long-running spikes",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    rows = _asyncio.run(load_feedback(tmp_path))
    assert len(rows) == 1
    assert rows[0].briefing_id == "brief-20260501T120000Z"
    assert rows[0].useful is False
    assert rows[0].not_useful_pattern_ids == ["tickets_stalled"]


def test_cli_quartermaster_feedback_rejects_unknown_pattern(tmp_path: Path):
    """Bad pattern id surfaces as a click ClickException."""
    from click.testing import CliRunner

    from jig.cli import cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "quartermaster",
            "feedback",
            "b-1",
            "--not-useful",
            "--noisy-pattern",
            "no_such_pattern",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "no_such_pattern" in result.output


# ---- role config carries record_feedback --------------------------------


def test_quartermaster_role_config_has_record_feedback(tmp_path: Path):
    """The shipped quartermaster role config exposes ``record_feedback``."""
    cfg = load_role(tmp_path, "quartermaster")
    assert "record_feedback" in cfg.allowed_tools


# ---- mcp registration ---------------------------------------------------


@pytest.mark.asyncio
async def test_record_feedback_mcp_tool_registered(tmp_path: Path, monkeypatch):
    import jig.mcp_server as mcp_server_mod
    from jig.mcp_server import create_agent_mcp_server
    from jig.models import RoleConfig
    from jig.store.bus import MessageBus
    from jig.store.memory import MemoryStore
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore

    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()

    cfg = RoleConfig(
        role="quartermaster",
        allowed_tools=["Read", "quartermaster_briefing", "record_feedback"],
        strict_tools=True,
    )
    captured: dict = {}
    real = mcp_server_mod.create_sdk_mcp_server

    def spy(*, name, tools):
        captured["tools"] = tools
        return real(name=name, tools=tools)

    monkeypatch.setattr(mcp_server_mod, "create_sdk_mcp_server", spy)

    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="quartermaster",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = {t.name for t in captured["tools"]}
    assert "record_feedback" in names
