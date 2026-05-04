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
