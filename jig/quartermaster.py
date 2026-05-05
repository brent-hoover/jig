"""Quartermaster — on-demand operator briefing.

Per ``docs/v2.0/agent-leverage/problem.md`` §3: a continuous background
agent that reads the analytics event stream and produces periodic
operator-facing briefings ("this week: 12 tickets completed, auth
module showing repeated escalations, here are 3 things I think need
your attention").

Design choices:

- **Pydantic v2 models** for every output structure so the briefing
  round-trips cleanly through MCP and the markdown formatter has a
  stable shape to render against.
- **Deterministic patterns only.** Three patterns ship: module
  repeated escalations, reviewer repeating one comment type, and
  tickets stalled past a threshold. Each pattern has a configurable
  threshold the calibration layer (Final scope) tunes per operator
  feedback.
- **Read-only against the AnalyticsStore.** The quartermaster never
  writes events; it only summarizes. The MCP role config bans every
  authoring tool to enforce this at the agent boundary.
- **TicketStore lookup is optional.** ``ticket_to_module`` lets
  callers pass a pre-built map (test scenarios, snapshot stores)
  rather than forcing a TicketStore dependency for the simple
  aggregation path. The MCP handler does the lookup against the
  project's TicketStore.

Final scope additions:

- ``BriefingFeedback`` Pydantic model + ``record_feedback`` MCP tool
  for the operator to mark briefings useful / not-useful and tag
  noisy patterns.
- ``PatternCalibration`` model holding tuned thresholds; mechanically
  updated based on accumulated feedback (no LLM judgment).
- Calibration update rules: each not_useful_pattern_id raises that
  pattern's threshold by 1 (capped at 2x default). After 30 days of
  no feedback the calibration drifts back toward defaults by 1 per
  threshold per 30-day window.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jig.analytics.events import (
    TicketGraphImpact,
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
    "BriefingFeedback",
    "HeadlineMetrics",
    "Pattern",
    "PatternCalibration",
    "Quartermaster",
    "WeeklyBriefing",
    "compute_calibration",
    "format_briefing",
    "get_pattern_calibration",
    "handle_quartermaster_briefing",
    "handle_record_feedback",
    "load_feedback",
    "record_feedback",
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
# Phase 4.11 — complex-tickets pattern: tickets crossing >= N module
# boundaries are flagged as architectural complexity signals.
_DEFAULT_COMPLEX_TICKET_BOUNDARY_THRESHOLD = 3

# Pattern severity ordering for attention-recommendation ranking.
# Higher index = higher severity. Module-escalation patterns beat
# stall patterns beat reviewer-noise patterns; ties broken by
# evidence count.
_PATTERN_SEVERITY_RANK: dict[str, int] = {
    "module_repeated_escalations": 30,
    "tickets_stalled": 20,
    "reviewer_repeating_comment_type": 10,
    "tickets_crossing_many_boundaries": 25,
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
    """The structured briefing the Quartermaster produces.

    ``id`` is a deterministic-per-period identifier the operator
    references when recording feedback. It defaults to a
    second-precision UTC string derived from ``period_end`` so the
    same period always gets the same id — a re-issued briefing can
    be tagged with feedback once rather than re-tagged.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", min_length=0)
    period_start: datetime
    period_end: datetime
    headline_metrics: HeadlineMetrics
    notable_patterns: list[Pattern] = Field(default_factory=list)
    attention_recommendations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _default_id_from_period_end(self) -> "WeeklyBriefing":
        if not self.id:
            object.__setattr__(self, "id", _briefing_id(self.period_end))
        return self


def _briefing_id(period_end: datetime) -> str:
    """Stable id for a briefing covering ``period_end``.

    Format: ``brief-YYYYMMDDTHHMMSSZ``. Deterministic on the
    period_end second so an operator who re-runs the briefing tag
    references the same row when recording feedback.
    """
    return "brief-" + period_end.strftime("%Y%m%dT%H%M%SZ")


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
        complex_ticket_boundary_threshold: int = _DEFAULT_COMPLEX_TICKET_BOUNDARY_THRESHOLD,
        calibration: "PatternCalibration | None" = None,
    ) -> None:
        self._analytics = analytics
        self._window_days = window_days
        self._ticket_to_module = ticket_to_module or {}
        # Calibration overrides constructor thresholds when present;
        # explicit constructor args still win for callers (tests) that
        # want a fixed value regardless of any on-disk feedback. The
        # MCP handler builds the Quartermaster with calibration only
        # so operator-tuned thresholds take effect; the lower-level
        # tests pass thresholds directly.
        if calibration is not None:
            module_escalation_threshold = calibration.threshold_for(
                "module_repeated_escalations"
            )
            reviewer_repeat_threshold = calibration.threshold_for(
                "reviewer_repeating_comment_type"
            )
            stall_threshold_days = calibration.threshold_for("tickets_stalled")
            complex_ticket_boundary_threshold = calibration.threshold_for(
                "tickets_crossing_many_boundaries"
            )
        self._module_escalation_threshold = module_escalation_threshold
        self._reviewer_repeat_threshold = reviewer_repeat_threshold
        self._stall_threshold_days = stall_threshold_days
        self._recommendation_limit = recommendation_limit
        self._complex_ticket_boundary_threshold = complex_ticket_boundary_threshold

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
        out.extend(self._pattern_complex_tickets(events))
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

    def _pattern_complex_tickets(self, events: list[AnalyticsEvent]) -> list[Pattern]:
        """Tickets whose ``crossed_boundaries`` meets or exceeds the threshold.

        A high boundary-crossing count is a signal that the ticket spans
        too many architectural seams — either the ticket is too large, or
        the module boundaries are wrong. One Pattern is emitted listing
        all offending tickets.
        """
        offenders: list[tuple[str, str]] = []
        for e in events:
            if not isinstance(e, TicketGraphImpact):
                continue
            if e.crossed_boundaries >= self._complex_ticket_boundary_threshold:
                offenders.append((e.ticket_id, e.id))

        if not offenders:
            return []
        ticket_ids = sorted(t for t, _ in offenders)
        return [
            Pattern(
                kind="tickets_crossing_many_boundaries",
                description=(
                    f"{len(offenders)} ticket(s) crossed >= "
                    f"{self._complex_ticket_boundary_threshold} module "
                    "boundaries — these may be too large or signal "
                    "incorrect module decomposition: "
                    + ", ".join(ticket_ids)
                ),
                evidence_event_ids=[eid for _, eid in offenders],
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
    lines.append(f"Briefing id: `{b.id}`")
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
    # Honor accumulated operator feedback by reading the calibration
    # off disk before constructing the Quartermaster — this is what
    # makes "this pattern is noisy on this project" actually take
    # effect across briefings without code changes (Track I Final).
    calibration = await get_pattern_calibration(project_path)

    qm = Quartermaster(
        analytics,
        ticket_to_module=ticket_to_module,
        calibration=calibration,
    )
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


# ---- feedback loop + calibration (Track I Final) -----------------------


# Pattern ids the operator can mark as "noisy". The set mirrors the
# ``Pattern.kind`` strings the deterministic detectors emit; the
# calibration applies one threshold per pattern id. Centralized so a
# typo in operator feedback fails at the MCP boundary rather than
# producing an un-tunable feedback row.
_PATTERN_IDS: frozenset[str] = frozenset(
    {
        "module_repeated_escalations",
        "reviewer_repeating_comment_type",
        "tickets_stalled",
        "tickets_crossing_many_boundaries",
    }
)


# Map pattern id → the Quartermaster constructor kwarg whose default
# the calibration multiplies/raises. Centralized so the wiring stays
# in one place; adding a new pattern means one entry here.
_PATTERN_TO_KWARG: dict[str, str] = {
    "module_repeated_escalations": "module_escalation_threshold",
    "reviewer_repeating_comment_type": "reviewer_repeat_threshold",
    "tickets_stalled": "stall_threshold_days",
    "tickets_crossing_many_boundaries": "complex_ticket_boundary_threshold",
}


# Default thresholds the calibration tracks against. Mirrors the
# constructor defaults; replicated here as a separate constant so
# the calibration math doesn't reach into the constructor signature.
_DEFAULT_THRESHOLDS: dict[str, int] = {
    "module_repeated_escalations": _DEFAULT_MODULE_ESCALATION_THRESHOLD,
    "reviewer_repeating_comment_type": _DEFAULT_REVIEWER_REPEAT_THRESHOLD,
    "tickets_stalled": _DEFAULT_STALL_THRESHOLD_DAYS,
    "tickets_crossing_many_boundaries": _DEFAULT_COMPLEX_TICKET_BOUNDARY_THRESHOLD,
}


# Calibration cap: thresholds drift up by 1 per "noisy" feedback hit
# but cap at this multiplier of the default. The cap keeps runaway
# feedback from raising thresholds into nonsense values (an operator
# repeatedly marking everything noisy shouldn't disable the pattern
# entirely).
_CALIBRATION_MAX_MULTIPLIER = 2

# Drift-back cadence: every N days of NO feedback on a pattern, the
# calibration moves that threshold one step back toward the default.
# Slow drift back means stale "this is noisy" signals don't lock the
# pattern into permanently-elevated thresholds when the project's
# noise profile changes.
_CALIBRATION_DRIFT_DAYS = 30


class BriefingFeedback(BaseModel):
    """One operator-supplied feedback record on a briefing.

    Append-only — every feedback call writes one row to
    ``feedback.jsonl``. The calibration aggregates over the rows
    rather than mutating a calibration document directly, so the
    audit trail stays intact (an operator can review what feedback
    accumulated up to a given calibration state).
    """

    model_config = ConfigDict(extra="forbid")

    briefing_id: str = Field(..., min_length=1)
    useful: bool
    not_useful_pattern_ids: list[str] = Field(default_factory=list)
    note: str | None = None
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class PatternCalibration(BaseModel):
    """Tuned thresholds derived from accumulated feedback.

    Each entry maps a pattern id to a tuned threshold. Patterns
    without entries fall back to ``_DEFAULT_THRESHOLDS``; the caller
    only consults this model when a pattern needs an override.
    """

    model_config = ConfigDict(extra="forbid")

    thresholds: dict[str, int] = Field(default_factory=dict)
    last_feedback_at: datetime | None = None

    def threshold_for(self, pattern_id: str) -> int:
        """Return the calibrated threshold or the shipped default."""
        if pattern_id in self.thresholds:
            return self.thresholds[pattern_id]
        return _DEFAULT_THRESHOLDS[pattern_id]


def _feedback_path(project_path: Path) -> Path:
    """Disk location of the feedback JSONL file."""
    return project_path / ".jig" / "quartermaster" / "feedback.jsonl"


async def _load_feedback_collection(project_path: Path):
    """Open the feedback Collection, creating its parent dir.

    Lazy-imports the Collection primitive so callers that only ever
    read briefings (no feedback) don't pay the import cost. The
    collection lives at ``.jig/quartermaster/feedback.jsonl`` rather
    than under ``.jig/store/`` because it's quartermaster-private —
    co-locating with the store dir would mix it with the analytics
    JSONL the quartermaster reads from.
    """
    from jig.store.collection import Collection

    path = _feedback_path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    collection = Collection(path, index_fields=["briefing_id"])
    await collection.load()
    return collection


async def load_feedback(project_path: Path) -> list[BriefingFeedback]:
    """Return every feedback row, oldest first.

    Used by the calibration aggregator + by tests that want to
    introspect the feedback trail. Sort key is the row's
    ``timestamp`` (the wall clock when the feedback was recorded);
    rows tied at the same instant fall back to insertion order.
    """
    collection = await _load_feedback_collection(project_path)
    rows = await collection.find()
    out: list[BriefingFeedback] = []
    for row in rows:
        # Strip the collection's bookkeeping ids before validating.
        payload = {k: v for k, v in row.items() if k != "_id"}
        out.append(BriefingFeedback.model_validate(payload))
    out.sort(key=lambda f: f.timestamp)
    return out


async def record_feedback(
    project_path: Path,
    *,
    briefing_id: str,
    useful: bool,
    not_useful_pattern_ids: list[str] | None = None,
    note: str | None = None,
) -> str:
    """Append one feedback row; return the new collection id.

    Validation:

    - Every entry in ``not_useful_pattern_ids`` must appear in the
      canonical ``_PATTERN_IDS`` set; unknown ids raise ValueError so
      a typo in the CLI fails loudly rather than silently producing
      an un-tunable row.
    - ``not_useful_pattern_ids`` should be empty when ``useful=True``
      (tagging "useful" while also marking patterns as noisy is
      ambiguous); we accept it but only the calibration aggregator
      decides the disposition (``useful=True`` with patterns named
      simply doesn't bump anything).
    """
    not_useful_pattern_ids = list(not_useful_pattern_ids or [])
    unknown = [p for p in not_useful_pattern_ids if p not in _PATTERN_IDS]
    if unknown:
        raise ValueError(
            f"record_feedback: unknown pattern id(s) {unknown!r}; "
            f"known: {sorted(_PATTERN_IDS)!r}"
        )
    fb = BriefingFeedback(
        briefing_id=briefing_id,
        useful=useful,
        not_useful_pattern_ids=not_useful_pattern_ids,
        note=note,
    )
    collection = await _load_feedback_collection(project_path)
    return await collection.insert(fb.model_dump(mode="json"))


def compute_calibration(
    feedback: list[BriefingFeedback],
    *,
    now: datetime | None = None,
) -> PatternCalibration:
    """Aggregate feedback into a calibrated threshold map.

    Mechanical (no LLM):

    1. Walk feedback oldest → newest. For each row with
       ``useful=False``, raise the threshold of every named
       not-useful pattern by 1 (capped at 2x default).
    2. Useful=True rows do not change thresholds.
    3. Drift-back: every 30 days since the last feedback row touched
       a given pattern, the threshold drifts one step back toward
       the default. Floor is the default itself (no drift below the
       shipped value).

    Pure function over the feedback list — no I/O — so tests can
    pin specific timestamps without setting up a project tree.
    """
    now = now or datetime.now(timezone.utc)
    thresholds: dict[str, int] = {}
    last_touched: dict[str, datetime] = {}
    last_feedback_at: datetime | None = None

    for fb in sorted(feedback, key=lambda f: f.timestamp):
        last_feedback_at = fb.timestamp
        if fb.useful:
            continue
        for pattern_id in fb.not_useful_pattern_ids:
            if pattern_id not in _DEFAULT_THRESHOLDS:
                # Validation at record time keeps this list clean,
                # but be defensive: skip unknown ids rather than
                # raising in the aggregator (we don't want a stale
                # JSONL row to break the briefing).
                continue
            current = thresholds.get(pattern_id, _DEFAULT_THRESHOLDS[pattern_id])
            cap = _DEFAULT_THRESHOLDS[pattern_id] * _CALIBRATION_MAX_MULTIPLIER
            thresholds[pattern_id] = min(current + 1, cap)
            last_touched[pattern_id] = fb.timestamp

    # Drift-back. Only applies to patterns that have been raised
    # above default; defaults stay at default regardless of age.
    for pattern_id, last in list(last_touched.items()):
        elapsed = now - last
        drift_steps = int(elapsed.total_seconds() // (_CALIBRATION_DRIFT_DAYS * 86400))
        if drift_steps <= 0:
            continue
        default = _DEFAULT_THRESHOLDS[pattern_id]
        thresholds[pattern_id] = max(
            thresholds[pattern_id] - drift_steps, default
        )
        if thresholds[pattern_id] == default:
            # Once the calibration drifts all the way back to default,
            # drop the entry — the briefing path falls back to default
            # for unset entries, so the empty model is the canonical
            # "fully drifted back" state.
            thresholds.pop(pattern_id)

    return PatternCalibration(
        thresholds=thresholds,
        last_feedback_at=last_feedback_at,
    )


async def get_pattern_calibration(
    project_path: Path, *, now: datetime | None = None
) -> PatternCalibration:
    """Public accessor: load feedback + compute the current calibration.

    Wraps ``load_feedback`` + ``compute_calibration`` so callers
    (the briefing path, the operator-facing CLI inspection command)
    don't have to know the two-step shape.
    """
    feedback = await load_feedback(project_path)
    return compute_calibration(feedback, now=now)


async def handle_record_feedback(
    *,
    project_path: Path,
    briefing_id: str,
    useful: bool,
    not_useful_pattern_ids: list[str] | None = None,
    note: str | None = None,
) -> str:
    """MCP entry point — record one feedback row; return its id.

    Thin wrapper over ``record_feedback`` so the MCP layer's
    handler-naming convention (``handle_<tool>``) is uniform.
    """
    return await record_feedback(
        project_path,
        briefing_id=briefing_id,
        useful=useful,
        not_useful_pattern_ids=not_useful_pattern_ids,
        note=note,
    )
