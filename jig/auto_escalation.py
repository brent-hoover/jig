"""Auto-escalation threshold checker (Track F MVP).

Per ``docs/v2.0/pm-workflow/design.md`` §"Auto-escalation thresholds": dev
agents systematically underclaim "I'm stuck." The Coordinator monitors
mechanical signals over the analytics event stream and force-escalates
when thresholds trip — single-digit-second latency, no LLM.

Design table (with conservative defaults):

| Threshold              | Signal                                                                   |
|------------------------|--------------------------------------------------------------------------|
| repeated_same_failure  | 3+ ``PerCommitCheckFailed`` on the same ``contract_uri``                 |
| tool_call_flailing     | 12+ ``ToolCalled`` events with the same ``args_digest``                  |
| no_commit_drift        | 30+ minutes since the last spawn with no commit signal                   |
| out_of_budget          | tier-budget exceeded by a factor configured in ``out_of_budget_pct``     |
| forced_reflection_at_minutes | every N minutes inject a self-eval prompt                          |

For MVP scope, the checker is invoked explicitly by the synthetic
operator (or, in a follow-on hook, the Orchestrator). Wiring into
``Orchestrator``'s per-ticket lifecycle is a separate hook task.

Out of MVP scope:
- The actual escalation action (pulling the agent, re-dispatching at
  the next tier). MVP fires the ``AutoEscalationTriggered`` analytics
  event so consumers / operator have visibility; the lifecycle action
  lands when the Coordinator wires into the per-ticket loop.
- Out-of-budget threshold's tier-expected envelope. The signal
  reads turns; calibration's per-tier band lands with the estimation
  loop.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import (
    AgentSpawned,
    AutoEscalationTriggered,
    PerCommitCheckFailed,
    ToolCalled,
)
from jig.analytics.store import AnalyticsStore

if TYPE_CHECKING:
    from jig.pm.calibration import CalibrationStore

__all__ = [
    "AutoEscalationConfig",
    "EscalationSignal",
    "check_escalation_signals",
    "emit_signals_as_events",
]


_logger = logging.getLogger(__name__)


# ---- config -------------------------------------------------------------


class AutoEscalationConfig(BaseModel):
    """Conservative default thresholds per design.md table.

    All fields tunable; analytics ``AutoEscalationTriggered`` events
    feed the recalibration judgment over time.
    """

    model_config = ConfigDict(extra="forbid")

    repeated_same_failure: int = Field(
        default=3, ge=1,
        description="N consecutive PerCommitCheckFailed on same contract URI.",
    )
    tool_call_flailing: int = Field(
        default=12, ge=1,
        description="N+ ToolCalled events with the same args_digest.",
    )
    no_commit_drift_minutes: int = Field(
        default=30, ge=1,
        description="Minutes since spawn with no commit-implying activity.",
    )
    out_of_budget_pct: float = Field(
        default=0.85, ge=0.0, le=10.0,
        description="Fraction of tier-budget consumed before tripping.",
    )
    forced_reflection_at_minutes: int = Field(
        default=20, ge=1,
        description="Periodic forced-reflection injection interval.",
    )


# ---- signals ------------------------------------------------------------


_SignalKind = Literal[
    "repeated_same_failure",
    "tool_call_flailing",
    "no_commit_drift",
    "out_of_budget",
    "forced_reflection_at_minutes",
]


# These map directly into ``AutoEscalationTriggered.trip_signal`` enum
# values per the analytics event schema. The event uses
# ``no_commit_drift`` (without ``_minutes``) and
# ``forced_reflection_no_progress``; we map at emit time.
_SIGNAL_TO_EVENT_TRIP: dict[str, str] = {
    "repeated_same_failure": "repeated_same_failure",
    "tool_call_flailing": "tool_call_flailing",
    "no_commit_drift": "no_commit_drift",
    "out_of_budget": "out_of_budget",
    "forced_reflection_at_minutes": "forced_reflection_no_progress",
}


class EscalationSignal(BaseModel):
    """One tripped threshold."""

    model_config = ConfigDict(extra="forbid")

    kind: _SignalKind
    threshold_value: float
    observed_value: float
    detail: str = ""


# ---- checker ------------------------------------------------------------


async def check_escalation_signals(
    ticket_id: str,
    analytics_store: AnalyticsStore,
    *,
    config: AutoEscalationConfig | None = None,
    now: datetime | None = None,
    calibration_store: "CalibrationStore | None" = None,
    ticket_size: str | None = None,
) -> list[EscalationSignal]:
    """Read recent analytics events for a ticket; report tripped thresholds.

    The checker is mechanical (no LLM): pull events from the store,
    apply each threshold's predicate, return the list of signals that
    tripped. Single-digit-second latency on bones-scale corpora.

    ``now`` lets tests pin a clock for time-windowed thresholds; in
    production it defaults to ``datetime.now(timezone.utc)``.
    """
    cfg = config or AutoEscalationConfig()
    clock = now or datetime.now(timezone.utc)

    signals: list[EscalationSignal] = []

    # Repeated same-failure: count per-commit-check-failed events
    # against this ticket, grouped by contract URI.
    failed = await analytics_store.by_kind("per_commit_check_failed")
    failed_for_ticket = [
        e for e in failed
        if isinstance(e, PerCommitCheckFailed) and e.ticket_id == ticket_id
    ]
    if failed_for_ticket:
        by_contract: Counter[str | None] = Counter()
        for ev in failed_for_ticket:
            by_contract[ev.contract_uri] += 1
        worst_contract, count = by_contract.most_common(1)[0]
        if count >= cfg.repeated_same_failure:
            signals.append(
                EscalationSignal(
                    kind="repeated_same_failure",
                    threshold_value=cfg.repeated_same_failure,
                    observed_value=count,
                    detail=(
                        f"{count} per-commit failures on "
                        f"contract={worst_contract!r}"
                    ),
                )
            )

    # The remaining thresholds key off the *most recent* AgentSpawned
    # for this ticket. If no spawn exists, none of the time-windowed
    # signals can trip.
    spawns = await analytics_store.by_kind("agent_spawned")
    spawns_for_ticket = sorted(
        (e for e in spawns if isinstance(e, AgentSpawned) and e.ticket_id == ticket_id),
        key=lambda e: e.timestamp,
    )
    if not spawns_for_ticket:
        return signals
    latest_spawn = spawns_for_ticket[-1]
    agent_id = latest_spawn.agent_id
    age = clock - latest_spawn.timestamp

    # Tool-call flailing: same args_digest repeated across this agent's
    # most-recent calls. ``cfg.tool_call_flailing`` is the trip count.
    tool_events = await analytics_store.by_kind("tool_called")
    agent_tool_events = [
        e for e in tool_events
        if isinstance(e, ToolCalled) and e.agent_id == agent_id
    ]
    if agent_tool_events:
        by_digest: Counter[str] = Counter(e.args_digest for e in agent_tool_events)
        worst_digest, repeat = by_digest.most_common(1)[0]
        if repeat >= cfg.tool_call_flailing:
            signals.append(
                EscalationSignal(
                    kind="tool_call_flailing",
                    threshold_value=cfg.tool_call_flailing,
                    observed_value=repeat,
                    detail=(
                        f"{repeat} identical tool calls "
                        f"(digest={worst_digest[:8]!r})"
                    ),
                )
            )

    # No-commit drift: agent spawned > N minutes ago and no commit
    # signal seen. We use the ToolCalled stream as a proxy for
    # commit activity (full per-commit tracking is a follow-on hook).
    if age >= timedelta(minutes=cfg.no_commit_drift_minutes):
        signals.append(
            EscalationSignal(
                kind="no_commit_drift",
                threshold_value=cfg.no_commit_drift_minutes,
                observed_value=age.total_seconds() / 60.0,
                detail=(
                    f"agent active for {age.total_seconds() / 60.0:.1f} "
                    "minutes with no commit signal"
                ),
            )
        )

    # Forced reflection: trip whenever the agent has been active longer
    # than the reflection window — caller is responsible for not
    # re-firing after a reflection has been injected.
    if age >= timedelta(minutes=cfg.forced_reflection_at_minutes):
        signals.append(
            EscalationSignal(
                kind="forced_reflection_at_minutes",
                threshold_value=cfg.forced_reflection_at_minutes,
                observed_value=age.total_seconds() / 60.0,
                detail=(
                    f"agent active for {age.total_seconds() / 60.0:.1f} "
                    "minutes — forced self-eval due"
                ),
            )
        )

    # Out-of-budget — Track F Final calibration integration. When a
    # CalibrationStore is supplied alongside the ticket's size, the
    # threshold uses the per-size p90 envelope from the calibration
    # store (when sample count meets the calibration minimum) rather
    # than the conservative shipped default. Trips when the agent's
    # observed turn count exceeds the p90 by more than the configured
    # ``out_of_budget_pct`` over-budget factor.
    if calibration_store is not None and ticket_size is not None:
        from jig.pm.calibration import (
            DEFAULT_ENVELOPES,
            current_envelopes,
            MIN_SAMPLES_FOR_CALIBRATION,
        )

        envelopes = current_envelopes(calibration_store)
        env = envelopes.get(ticket_size) or DEFAULT_ENVELOPES.get(ticket_size)
        if env is not None:
            calibrated = env.sample_count >= MIN_SAMPLES_FOR_CALIBRATION
            observed_turns = float(len(agent_tool_events))
            # The signal trips when observed turns exceed the threshold
            # of (p90 + over-budget pct headroom).
            budget_threshold = env.p90_turns * (1.0 + cfg.out_of_budget_pct)
            if budget_threshold > 0 and observed_turns >= budget_threshold:
                source = "calibrated" if calibrated else "default"
                signals.append(
                    EscalationSignal(
                        kind="out_of_budget",
                        threshold_value=budget_threshold,
                        observed_value=observed_turns,
                        detail=(
                            f"observed_turns={int(observed_turns)} >= "
                            f"{budget_threshold:.1f} "
                            f"(p90={env.p90_turns:.1f} via {source} "
                            f"envelope for size={ticket_size!r})"
                        ),
                    )
                )

    return signals


# ---- emit helper --------------------------------------------------------


def emit_signals_as_events(
    emitter: EventEmitter,
    *,
    signals: list[EscalationSignal],
    ticket_id: str,
    agent_id: str,
    from_tier: Literal["standard", "senior", "sa"],
    turns_at_trip: int = 0,
) -> None:
    """Fire one ``AutoEscalationTriggered`` event per tripped signal.

    Single hop helper — caller passes the signals from
    ``check_escalation_signals`` and the emitter wires it onto the
    analytics store. Empty signals list is a no-op.

    Tier promotion target: ``standard → senior``, ``senior → sa``,
    ``sa → operator``. The actual lifecycle action (pulling the agent
    + re-dispatching) lands in the orchestrator hook task; this
    function only fires the analytics event so the operator + corpus
    have visibility.
    """
    if not signals:
        return
    to_tier_map: dict[str, Literal["senior", "sa", "operator"]] = {
        "standard": "senior",
        "senior": "sa",
        "sa": "operator",
    }
    to_tier = to_tier_map[from_tier]
    for signal in signals:
        trip_signal = _SIGNAL_TO_EVENT_TRIP[signal.kind]
        emitter.emit_nowait(
            AutoEscalationTriggered(
                ticket_id=ticket_id,
                agent_id=agent_id,
                from_tier=from_tier,
                to_tier=to_tier,
                trip_signal=trip_signal,  # type: ignore[arg-type]
                trip_metric_value=signal.observed_value,
                turns_at_trip=turns_at_trip,
            )
        )
