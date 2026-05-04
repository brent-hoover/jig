"""Quartermaster — on-demand operator briefing (Track I MVP).

Per ``docs/agent-leverage/problem.md`` §3: a continuous background
agent that reads the analytics event stream and produces periodic
operator-facing briefings ("this week: 12 tickets completed, auth
module showing repeated escalations, here are 3 things I think need
your attention"). MVP scope: deterministic aggregation only — no LLM,
no continuous loop. The operator invokes ``handle_quartermaster_briefing``
on demand; the LLM-judgment additions and the continuous-loop wiring
land at Final scope.

Design choices:

- **Pydantic v2 models** for every output structure so the briefing
  round-trips cleanly through MCP and the markdown formatter has a
  stable shape to render against.
- **Deterministic patterns only.** Three patterns ship in MVP:
  module repeated escalations, reviewer repeating one comment type,
  and tickets stalled past a threshold. Each pattern has a
  configurable threshold so future calibration can tune without code
  changes.
- **Read-only against the AnalyticsStore.** The quartermaster never
  writes events; it only summarizes. The MCP role config bans every
  authoring tool to enforce this at the agent boundary.
- **TicketStore lookup is optional.** ``ticket_to_module`` lets
  callers pass a pre-built map (test scenarios, snapshot stores)
  rather than forcing a TicketStore dependency for the simple
  aggregation path. The MCP handler does the lookup against the
  project's TicketStore.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jig.analytics.events import (
    AgentCompleted,
    AnalyticsEvent,
    AutoEscalationTriggered,
    EscalationRouted,
    ReviewCommentPosted,
    TicketStateChanged,
)
from jig.analytics.store import AnalyticsStore

__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "HeadlineMetrics",
    "Pattern",
    "Quartermaster",
    "WeeklyBriefing",
    "format_briefing",
    "handle_quartermaster_briefing",
]


# Default briefing window. 7 days mirrors the docs §3 example
# ("this week"); operator can override per call. Configurable via
# Quartermaster constructor rather than a module-global constant
# because future per-operator cadence (open question #2 in docs)
# will plumb through the same path.
DEFAULT_WINDOW_DAYS = 7

# Pattern thresholds. All deterministic; bump or expose per-project
# once we have enough briefing-feedback signal to calibrate.
_DEFAULT_MODULE_ESCALATION_THRESHOLD = 3
_DEFAULT_REVIEWER_REPEAT_THRESHOLD = 3
_DEFAULT_STALL_THRESHOLD_DAYS = 3
_DEFAULT_RECOMMENDATION_LIMIT = 3

# Pattern severity ordering for attention-recommendation ranking.
# Higher index = higher severity. Module-escalation patterns beat
# stall patterns beat reviewer-noise patterns; ties broken by
# evidence count.
_PATTERN_SEVERITY_RANK: dict[str, int] = {
    "module_repeated_escalations": 30,
    "tickets_stalled": 20,
    "reviewer_repeating_comment_type": 10,
}


# ---- output models ------------------------------------------------------


class HeadlineMetrics(BaseModel):
    """Top-of-briefing headline numbers.

    Keep this thin — the operator should be able to read the headline
    in two seconds. The ``notable_patterns`` section carries detail.
    """

    model_config = ConfigDict(extra="forbid")

    tickets_completed: int = Field(..., ge=0)
    tickets_failed: int = Field(..., ge=0)
    tickets_in_progress: int = Field(..., ge=0)
    escalations: int = Field(..., ge=0)
    avg_cycle_time_seconds: float | None = Field(
        default=None,
        description=(
            "Average AgentCompleted.duration_ms across successful runs "
            "in-window, expressed in seconds. None when no successful "
            "runs occurred — operator sees 'n/a' rather than a "
            "misleading zero."
        ),
    )


class Pattern(BaseModel):
    """One deterministic pattern surfaced from the event stream.

    ``kind`` is the structured tag the formatter and recommendation
    logic switch on. ``description`` is the human-readable line shown
    in the briefing. ``evidence_event_ids`` give the operator a
    handle to pull the underlying events when investigating.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    evidence_event_ids: list[str] = Field(default_factory=list)


class WeeklyBriefing(BaseModel):
    """The structured briefing the Quartermaster produces."""

    model_config = ConfigDict(extra="forbid")

    period_start: datetime
    period_end: datetime
    headline_metrics: HeadlineMetrics
    notable_patterns: list[Pattern] = Field(default_factory=list)
    attention_recommendations: list[str] = Field(default_factory=list)


# ---- quartermaster ------------------------------------------------------


class Quartermaster:
    """Aggregates analytics events into an operator briefing.

    Stateless past construction. Each ``briefing()`` call walks the
    full event stream once, filters to the window, and runs the
    deterministic pattern checks. Acceptable in MVP because event
    volume is bounded by project scope (per AnalyticsStore docs); a
    streaming aggregator lands when corpora grow across projects.

    ``ticket_to_module`` is optional — tests pass a pre-built map;
    the MCP handler builds it from the live TicketStore. The
    quartermaster never opens a TicketStore directly so it doesn't
    pull in the orchestrator dependency tree.
    """

    def __init__(
        self,
        analytics: AnalyticsStore,
        *,
        window_days: int = DEFAULT_WINDOW_DAYS,
        ticket_to_module: dict[str, str] | None = None,
        module_escalation_threshold: int = _DEFAULT_MODULE_ESCALATION_THRESHOLD,
        reviewer_repeat_threshold: int = _DEFAULT_REVIEWER_REPEAT_THRESHOLD,
        stall_threshold_days: int = _DEFAULT_STALL_THRESHOLD_DAYS,
        recommendation_limit: int = _DEFAULT_RECOMMENDATION_LIMIT,
    ) -> None:
        self._analytics = analytics
        self._window_days = window_days
        self._ticket_to_module = ticket_to_module or {}
        self._module_escalation_threshold = module_escalation_threshold
        self._reviewer_repeat_threshold = reviewer_repeat_threshold
        self._stall_threshold_days = stall_threshold_days
        self._recommendation_limit = recommendation_limit

    async def briefing(
        self,
        *,
        period_end: datetime | None = None,
    ) -> WeeklyBriefing:
        """Produce a briefing for the trailing ``window_days`` ending at ``period_end``.

        ``period_end`` defaults to ``datetime.now(timezone.utc)``.
        Tests pin a specific endpoint; the MCP handler always uses
        "now".
        """
        period_end = period_end or datetime.now(timezone.utc)
        period_start = period_end - timedelta(days=self._window_days)

        all_events = await self._analytics.all()
        in_window = [
            e for e in all_events if period_start <= e.timestamp <= period_end
        ]

        metrics = self._headline_metrics(in_window)
        patterns = self._detect_patterns(in_window)
        recommendations = self._recommendations(patterns)

        return WeeklyBriefing(
            period_start=period_start,
            period_end=period_end,
            headline_metrics=metrics,
            notable_patterns=patterns,
            attention_recommendations=recommendations,
        )

    # ---- aggregations ----------------------------------------------------

    def _headline_metrics(self, events: list[AnalyticsEvent]) -> HeadlineMetrics:
        completed = failed = in_progress = escalations = 0
        durations: list[int] = []

        for e in events:
            if isinstance(e, TicketStateChanged):
                if e.to_state == "resolved":
                    completed += 1
                elif e.to_state == "failed":
                    failed += 1
                elif e.to_state == "in_progress":
                    in_progress += 1
            elif isinstance(e, (EscalationRouted, AutoEscalationTriggered)):
                escalations += 1
            elif isinstance(e, AgentCompleted) and e.status == "success":
                durations.append(e.duration_ms)

        avg = sum(durations) / len(durations) / 1000.0 if durations else None
        return HeadlineMetrics(
            tickets_completed=completed,
            tickets_failed=failed,
            tickets_in_progress=in_progress,
            escalations=escalations,
            avg_cycle_time_seconds=avg,
        )

    def _detect_patterns(self, events: list[AnalyticsEvent]) -> list[Pattern]:
        out: list[Pattern] = []
        out.extend(self._pattern_module_escalations(events))
        out.extend(self._pattern_reviewer_repeats(events))
        out.extend(self._pattern_stalled_tickets(events))
        return out

    def _pattern_module_escalations(self, events: list[AnalyticsEvent]) -> list[Pattern]:
        """Modules with >= N escalations in-window. Resolves ticket → module
        via the caller-supplied map; tickets with no module mapping are
        skipped (we'd otherwise miscount unmapped tickets as one bucket).
        """
        per_module: dict[str, list[str]] = defaultdict(list)
        for e in events:
            if not isinstance(e, (EscalationRouted, AutoEscalationTriggered)):
                continue
            ticket_id = e.ticket_id
            if ticket_id is None:
                continue
            module = self._ticket_to_module.get(ticket_id)
            if module is None:
                continue
            per_module[module].append(e.id)

        out: list[Pattern] = []
        for module, evidence in per_module.items():
            if len(evidence) >= self._module_escalation_threshold:
                out.append(
                    Pattern(
                        kind="module_repeated_escalations",
                        description=(
                            f"Module {module!r} accumulated {len(evidence)} "
                            "escalations this period — repeated escalation "
                            "on the same module is a signal that the "
                            "contracts may need amendment or the dev tier "
                            "is wrong."
                        ),
                        evidence_event_ids=evidence,
                    )
                )
        return out

    def _pattern_reviewer_repeats(self, events: list[AnalyticsEvent]) -> list[Pattern]:
        """One (reviewer_id, comment_type) firing >= N times in-window.

        Signal: either the reviewer is well-tuned and the agents keep
        making the same mistake, or the check itself is firing on
        false-positives. Either way the operator wants to know.
        """
        counts: Counter[tuple[str, str]] = Counter()
        evidence: dict[tuple[str, str], list[str]] = defaultdict(list)
        for e in events:
            if not isinstance(e, ReviewCommentPosted):
                continue
            key = (e.reviewer_id, e.comment_type)
            counts[key] += 1
            evidence[key].append(e.id)

        out: list[Pattern] = []
        for (reviewer_id, comment_type), n in counts.items():
            if n >= self._reviewer_repeat_threshold:
                out.append(
                    Pattern(
                        kind="reviewer_repeating_comment_type",
                        description=(
                            f"Reviewer {reviewer_id!r} fired "
                            f"{comment_type!r} {n} times this period — "
                            "either agents repeatedly miss the same check "
                            "or the check is over-firing."
                        ),
                        evidence_event_ids=evidence[(reviewer_id, comment_type)],
                    )
                )
        return out

    def _pattern_stalled_tickets(self, events: list[AnalyticsEvent]) -> list[Pattern]:
        """Tickets in_progress for > stall_threshold_days with no terminal
        state since. Window-relative: we look at the last in-window state
        transition per ticket; if it's in_progress and older than the
        threshold (relative to now), the ticket is stalled.

        Note: a ticket whose in_progress transition predates the window
        won't appear here — the briefing is intentionally window-scoped.
        Cross-window stall tracking lands when we have persistent
        baselines.
        """
        last_state_per_ticket: dict[str, TicketStateChanged] = {}
        for e in events:
            if not isinstance(e, TicketStateChanged):
                continue
            current = last_state_per_ticket.get(e.ticket_id)
            if current is None or e.timestamp > current.timestamp:
                last_state_per_ticket[e.ticket_id] = e

        now = datetime.now(timezone.utc)
        threshold = timedelta(days=self._stall_threshold_days)
        stalled: list[tuple[str, str]] = []
        for ticket_id, ev in last_state_per_ticket.items():
            if ev.to_state != "in_progress":
                continue
            if now - ev.timestamp >= threshold:
                stalled.append((ticket_id, ev.id))

        if not stalled:
            return []
        return [
            Pattern(
                kind="tickets_stalled",
                description=(
                    f"{len(stalled)} ticket(s) stalled in_progress past "
                    f"the {self._stall_threshold_days}-day threshold: "
                    + ", ".join(sorted(t for t, _ in stalled))
                ),
                evidence_event_ids=[eid for _, eid in stalled],
            )
        ]

    def _recommendations(self, patterns: list[Pattern]) -> list[str]:
        """Top-N attention items, ranked by severity then evidence count.

        Returned as short prose strings (not Pattern instances) so the
        operator-facing markdown can treat them as a flat bullet list.
        """
        ranked = sorted(
            patterns,
            key=lambda p: (
                _PATTERN_SEVERITY_RANK.get(p.kind, 0),
                len(p.evidence_event_ids),
            ),
            reverse=True,
        )
        return [f"{p.kind}: {p.description}" for p in ranked[: self._recommendation_limit]]


# ---- formatter ---------------------------------------------------------


def format_briefing(b: WeeklyBriefing) -> str:
    """Render the briefing as operator-facing markdown.

    Skim-able by design: header → headline metrics → notable patterns
    → attention recommendations. No emojis, no clever formatting; the
    operator reads this in a terminal.
    """
    lines: list[str] = []
    lines.append("# Quartermaster Briefing")
    lines.append("")
    lines.append(
        f"Period: {b.period_start.isoformat(timespec='seconds')} → "
        f"{b.period_end.isoformat(timespec='seconds')}"
    )
    lines.append("")

    lines.append("## Headline metrics")
    lines.append("")
    lines.append(f"- Tickets completed: {b.headline_metrics.tickets_completed}")
    lines.append(f"- Tickets failed: {b.headline_metrics.tickets_failed}")
    lines.append(f"- Tickets in progress: {b.headline_metrics.tickets_in_progress}")
    lines.append(f"- Escalations: {b.headline_metrics.escalations}")
    if b.headline_metrics.avg_cycle_time_seconds is None:
        lines.append("- Average cycle time: n/a (no successful agent runs in window)")
    else:
        lines.append(
            f"- Average cycle time: {b.headline_metrics.avg_cycle_time_seconds:.1f}s"
        )
    lines.append("")

    lines.append("## Notable patterns")
    lines.append("")
    if not b.notable_patterns:
        lines.append("- (no notable patterns this period)")
    else:
        for p in b.notable_patterns:
            lines.append(f"- **{p.kind}** — {p.description}")
            if p.evidence_event_ids:
                lines.append(
                    f"  - Evidence: {len(p.evidence_event_ids)} event(s)"
                )
    lines.append("")

    lines.append("## Attention recommendations")
    lines.append("")
    if not b.attention_recommendations:
        lines.append("- (nothing to report)")
    else:
        for rec in b.attention_recommendations:
            lines.append(f"- {rec}")
    lines.append("")

    return "\n".join(lines)


# ---- mcp handler -------------------------------------------------------


async def handle_quartermaster_briefing(
    *,
    project_path: Path,
) -> str:
    """MCP entry point — load analytics + tickets, run, return markdown.

    Tolerates a missing analytics file (the project hasn't run any
    agents yet) by returning a zero-counts briefing rather than
    raising — the operator can call quartermaster on day zero and
    see "nothing yet" instead of an error.
    """
    analytics_path = project_path / ".jig" / "store" / "analytics.jsonl"
    analytics = AnalyticsStore(analytics_path)
    await analytics.load()

    ticket_to_module = await _load_ticket_to_module(project_path)

    qm = Quartermaster(analytics, ticket_to_module=ticket_to_module)
    briefing = await qm.briefing()
    return format_briefing(briefing)


async def _load_ticket_to_module(project_path: Path) -> dict[str, str]:
    """Best-effort ticket → module map from the live TicketStore.

    Returns an empty map when the ticket store doesn't exist (project
    hasn't been initialized yet) so the quartermaster still produces
    a valid briefing — ``module_repeated_escalations`` simply won't
    fire without a module mapping.
    """
    tickets_path = project_path / ".jig" / "store" / "tickets.jsonl"
    if not tickets_path.is_file():
        return {}
    # Lazy import to keep the simple Quartermaster() construction path
    # free of orchestrator dependencies.
    from jig.store.tickets import TicketStore

    store = TicketStore(tickets_path)
    await store.load()
    out: dict[str, str] = {}
    for t in await store.list_all():
        if t.module_id:
            out[t.id] = t.module_id
    return out
