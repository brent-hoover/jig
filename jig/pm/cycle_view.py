"""Cycle visualization data model (Track F Final, deliverable 4).

Per ``docs/v2.0/pm-workflow/design.md`` §"Iteration": the operator wants
one view that shows the current cycle state — per-epic layer progress,
the Coordinator's view of what's next, pending escalations, recent
tier promotions, and the calibration envelopes Planner reads when
sizing.

This module ships the data model + a markdown-rendering helper for
CLI inspection. The TUI track will consume the same model when it
renders the cycle view in the terminal.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jig.auto_escalation import EscalationSignal
from jig.coordinator import Coordinator
from jig.pm.calibration import CalibrationStore, Envelope, current_envelopes
from jig.pm.tier_promotion import TierPromotion
from jig.schemas.plan import (
    BuildPlan,
    EpicLayers,
    LayerName,
    LayerStatusEnum,
    OrderingRule,
)
from jig.spec_loader import load_build_plan
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus

__all__ = [
    "CoordinatorState",
    "CycleViewModel",
    "EpicViewModel",
    "LayerProgress",
    "build_cycle_view",
    "format_cycle_view",
]


class LayerProgress(BaseModel):
    """Per-layer ticket counts for an epic."""

    model_config = ConfigDict(extra="forbid")

    status: str
    tickets_total: int = 0
    tickets_resolved: int = 0
    tickets_in_progress: int = 0
    tickets_blocked: int = 0
    tickets_failed: int = 0
    tickets_deferred: int = 0


class EpicViewModel(BaseModel):
    """One epic's per-layer progress summary."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    layer_progress: dict[str, LayerProgress] = Field(default_factory=dict)


class CoordinatorState(BaseModel):
    """Snapshot of the Coordinator's deterministic decisions."""

    model_config = ConfigDict(extra="forbid")

    next_layer_ready: str | None = None
    ordering_rule: str = OrderingRule.BONES_FIRST.value
    holding_for_cascades: list[str] = Field(default_factory=list)


class CycleViewModel(BaseModel):
    """Aggregate cycle view — what the TUI + ``jig pm view`` consume."""

    model_config = ConfigDict(extra="forbid")

    cycle_revision: int = 1
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    epics: list[EpicViewModel] = Field(default_factory=list)
    coordinator_state: CoordinatorState = Field(default_factory=CoordinatorState)
    auto_escalations_pending: list[EscalationSignal] = Field(default_factory=list)
    recent_promotions: list[TierPromotion] = Field(default_factory=list)
    calibration_envelopes: dict[str, Envelope] = Field(default_factory=dict)


# ---- builder ------------------------------------------------------------


async def build_cycle_view(
    coordinator: Coordinator,
    project_root: Path,
    *,
    tickets: TicketStore | None = None,
) -> CycleViewModel:
    """Construct a ``CycleViewModel`` from the live build plan + stores.

    Reads the build plan (returns an empty view when missing), walks
    each epic's layers to count tickets per status, and queries the
    Coordinator + calibration store for the surfaced state. Pending
    escalations + recent promotions are best-effort: in scope here is
    the structural plumbing, callers compose their own signal lists.

    ``tickets`` defaults to the Coordinator's internal store; tests
    can pass their own to count tickets without poking at private
    attributes.
    """
    try:
        plan = load_build_plan(project_root)
    except FileNotFoundError:
        return CycleViewModel()

    ticket_store = tickets or coordinator._tickets  # type: ignore[attr-defined]

    epic_models: list[EpicViewModel] = []
    for epic in plan.epics:
        layers = await _layer_progress_for_epic(epic.layers, ticket_store)
        epic_models.append(
            EpicViewModel(
                id=epic.id, title=epic.title, layer_progress=layers,
            )
        )

    coord_state = CoordinatorState(
        next_layer_ready=coordinator.next_layer_ready(plan),
        ordering_rule=plan.ordering_rule.value,
        holding_for_cascades=_holding_for_cascades(plan),
    )

    cal_store = CalibrationStore(project_root)
    await cal_store.load()
    envelopes = current_envelopes(cal_store)

    return CycleViewModel(
        cycle_revision=plan.revision,
        epics=epic_models,
        coordinator_state=coord_state,
        calibration_envelopes=envelopes,
    )


async def _layer_progress_for_epic(
    layers: EpicLayers, tickets: TicketStore,
) -> dict[str, LayerProgress]:
    out: dict[str, LayerProgress] = {}
    for layer_name in (LayerName.BONES, LayerName.MVP, LayerName.FINAL):
        layer = (
            layers.bones if layer_name == LayerName.BONES
            else layers.mvp if layer_name == LayerName.MVP
            else layers.final
        )
        progress = LayerProgress(status=layer.status.value)
        progress.tickets_total = len(layer.tickets)
        for ticket_id in layer.tickets:
            t: Ticket | None = await tickets.get(ticket_id)
            if t is None:
                continue
            if t.status == TicketStatus.RESOLVED:
                progress.tickets_resolved += 1
            elif t.status == TicketStatus.IN_PROGRESS:
                progress.tickets_in_progress += 1
            elif t.status == TicketStatus.BLOCKED:
                progress.tickets_blocked += 1
            elif t.status == TicketStatus.FAILED:
                progress.tickets_failed += 1
            if t.deferred_at is not None:
                progress.tickets_deferred += 1
        out[layer_name.value] = progress
    return out


def _holding_for_cascades(plan: BuildPlan) -> list[str]:
    """List epic ids whose bones is blocked-but-not-done.

    Used by the cycle view to show "we're holding here pending an
    SA cascade decision." Empty list when nothing is holding.
    """
    out: list[str] = []
    for epic in plan.epics:
        layer = epic.layers.bones
        if not layer.tickets:
            continue
        if layer.status == LayerStatusEnum.BLOCKED:
            out.append(epic.id)
    return out


# ---- markdown rendering -------------------------------------------------


def format_cycle_view(view: CycleViewModel) -> str:
    """Render a ``CycleViewModel`` to an operator-readable markdown string."""
    lines: list[str] = []
    lines.append("# Cycle view")
    lines.append("")
    lines.append(
        f"Revision: {view.cycle_revision} | "
        f"Generated: {view.generated_at.isoformat(timespec='seconds')}"
    )
    lines.append("")

    lines.append("## Coordinator state")
    lines.append("")
    cs = view.coordinator_state
    lines.append(f"- next_layer_ready: `{cs.next_layer_ready or 'all-done'}`")
    lines.append(f"- ordering_rule: `{cs.ordering_rule}`")
    if cs.holding_for_cascades:
        lines.append(
            f"- holding_for_cascades: {', '.join(cs.holding_for_cascades)}"
        )
    else:
        lines.append("- holding_for_cascades: _(none)_")
    lines.append("")

    lines.append("## Epics")
    lines.append("")
    if not view.epics:
        lines.append("_(no epics in build plan)_")
        lines.append("")
    for epic in view.epics:
        lines.append(f"### `{epic.id}` — {epic.title}")
        lines.append("")
        lines.append(
            "| Layer | Status | Total | Resolved | In progress | Blocked | "
            "Failed | Deferred |"
        )
        lines.append("|-------|--------|-------|----------|-------------|"
                     "---------|--------|----------|")
        for layer_name in ("bones", "mvp", "final"):
            p = epic.layer_progress.get(layer_name)
            if p is None:
                continue
            lines.append(
                f"| {layer_name} | {p.status} | {p.tickets_total} | "
                f"{p.tickets_resolved} | {p.tickets_in_progress} | "
                f"{p.tickets_blocked} | {p.tickets_failed} | "
                f"{p.tickets_deferred} |"
            )
        lines.append("")

    lines.append("## Pending auto-escalations")
    lines.append("")
    if not view.auto_escalations_pending:
        lines.append("_(none pending)_")
    else:
        for sig in view.auto_escalations_pending:
            lines.append(
                f"- `{sig.kind}`: observed={sig.observed_value:.1f} / "
                f"threshold={sig.threshold_value:.1f} — {sig.detail}"
            )
    lines.append("")

    lines.append("## Recent tier promotions")
    lines.append("")
    if not view.recent_promotions:
        lines.append("_(none recent)_")
    else:
        for p in view.recent_promotions:
            lines.append(
                f"- `{p.ticket_id}`: {p.from_tier} → {p.to_tier} ({p.reason})"
            )
    lines.append("")

    lines.append("## Calibration envelopes")
    lines.append("")
    if not view.calibration_envelopes:
        lines.append("_(no calibration data)_")
    else:
        lines.append(
            "| Size | Samples | Median turns | P90 turns | "
            "Median cost | P90 cost |"
        )
        lines.append("|------|---------|--------------|-----------|"
                     "-------------|----------|")
        for size in ("xs", "s", "m", "l", "xl"):
            env = view.calibration_envelopes.get(size)
            if env is None:
                continue
            lines.append(
                f"| {size} | {env.sample_count} | {env.median_turns:.1f} | "
                f"{env.p90_turns:.1f} | "
                f"${env.median_cost_usd:.2f} | ${env.p90_cost_usd:.2f} |"
            )
    lines.append("")

    return "\n".join(lines)
