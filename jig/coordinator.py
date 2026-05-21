"""Coordinator PM — bones dispatch glue + cycle-aware multi-layer dispatcher.

The v2 PM splits in two: a **Planner** (strategic, runs in passes — Track
F2/F3) and a **Coordinator** (tactical, continuous — this module).

Two phases of capability live here:

1. **Bones (F4)** — ``materialize_ready_tickets`` materializes the
   bones-layer tickets one-shot from the operator-authored build plan.
   Kept intact for backward compatibility.
2. **MVP (F follow-on)** — ``materialize_layer``, ``advance_layer_status``,
   ``next_layer_ready``, ``dispatch_cycle`` extend the Coordinator into a
   cycle-aware multi-layer dispatcher that walks bones → mvp → final per
   the build plan's ``OrderingRule``. Plus DEFERRED queue triage helpers.

What this DOESN'T do (intentionally):

- Run as a continuous background loop. Operator (and tests) invoke
  ``dispatch_cycle`` after each dev completion. Wiring into
  ``Orchestrator.startup`` is a separate hook task.
- Spawn agents directly. The Coordinator only seeds the store; the
  orchestrator's existing ``find_ready`` + ``_handle_schedule`` path
  handles dispatch.
- Auto-escalate from inside the cycle. Auto-escalation is its own
  module (``jig.auto_escalation``) — the synthetic operator (and
  future Orchestrator hook) invokes the checker between cycles.

Track C Final adds the bones-first override path on
``next_layer_ready``: when every blocked epic's bones touches only
``cascade_risk_low=true`` modules, the MVP layer becomes promotable
for the un-blocked epics. The strict bones-first default still wins
when blocked epics touch any non-low-risk module.

See ``docs/v2.0/pm-workflow/design.md`` §"Roles" for the Planner/Coordinator
distinction, §"The three completeness layers" for the bones/mvp/final
ordering rules, and §"DEFERRED queue triage" for the deferred-queue flow.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from typing import TYPE_CHECKING

from jig.atomic import atomic_write_text
from jig.schemas.plan import (
    BuildPlan,
    Epic,
    LayerName,
    LayerStatus,
    LayerStatusEnum,
    OrderingRule,
)

if TYPE_CHECKING:
    from jig.analytics.emitter import EventEmitter
from jig.spec_loader import load_architecture, load_build_plan, write_build_plan
from jig.store.tickets import TicketStore
from jig.ticket import Size, Ticket, TicketStatus, WorkType

__all__ = [
    "BONES_DEFAULT_DEV_TIER",
    "Coordinator",
    "CycleResult",
    "DEFAULT_AUTHOR",
    "DeferredEntry",
    "TriageDecision",
]

# Author tag for tickets the Coordinator materializes. Distinct from
# ``"sa-v2"`` / ``"po-v2"`` so analytics + thread queries can attribute
# coordinator-created tickets to the PM lane.
DEFAULT_AUTHOR = "coordinator-v2"

# Bones default per design §"Bones tier defaults". For MVP / Final
# materialization we still default to standard — Planner-set
# ``dev_tier`` lands in a follow-on hook.
BONES_DEFAULT_DEV_TIER = "standard"

# Layer order per ``LayerName``. Bones-first ordering walks this in
# order across all epics; per-epic walks each epic's own chain.
_LAYER_ORDER: tuple[str, ...] = (
    LayerName.BONES.value,
    LayerName.MVP.value,
    LayerName.FINAL.value,
)


# Shared helper imported lazily to avoid heavy imports at module load.


class CycleResult(BaseModel):
    """Summary of one ``dispatch_cycle`` call.

    ``tickets_materialized`` — newly created ticket ids in this cycle.
    ``layers_advanced`` — list of ``(epic_id, layer, new_status)`` tuples
    for every layer whose status changed.
    ``next_layer`` — the layer the Coordinator would materialize next
    (after this cycle's materialization), or ``None`` when all done.
    """

    model_config = ConfigDict(extra="forbid")

    tickets_materialized: list[str] = Field(default_factory=list)
    layers_advanced: list[tuple[str, str, str]] = Field(default_factory=list)
    next_layer: str | None = None


# ---- DEFERRED queue models ----------------------------------------------


_DEFERRED_QUEUE_RELATIVE = Path(".jig") / "plan" / "deferred-queue.jsonl"


class DeferredEntry(BaseModel):
    """One row in the DEFERRED queue.

    Persisted as JSONL at ``.jig/plan/deferred-queue.jsonl``. Keep the
    payload small + structured — full reviewer comments live in their
    own store; this is the index.
    """

    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    reason: str
    notes: str = ""
    deferred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TriageDecision(BaseModel):
    """One row in a triage pass over the DEFERRED queue.

    For MVP triage is operator-driven: ``recommended_action`` is the
    Coordinator's mechanical suggestion (no LLM). The operator (or, in
    Final, the Planner LLM) picks the actual action.
    """

    model_config = ConfigDict(extra="forbid")

    entry: DeferredEntry
    recommended_action: Literal["rematerialize", "leave_deferred", "close"] = (
        "leave_deferred"
    )
    rationale: str = ""


def _synthesize_description_with_ac(*, problem: str, epic_title: str) -> str:
    """Build a Ticket description that satisfies the AC-required validator.

    Transitional helper for ``Coordinator._build_ticket_from_epic`` — the
    current ``Epic`` schema (``jig.schemas.plan.Epic``) has no
    ``acceptance_criteria`` field, so we derive a single-bullet AC from
    the epic's intent prose. Follow-on work adds explicit ACs to Epic
    and removes this synthesis step.

    The result interleaves the original intent prose with a discoverable
    ``## Acceptance criteria`` section, structured so the reviewer-test-
    adequacy reviewer treats the bullet as the AC scope for this ticket.
    """
    bullet_source = problem.strip() or epic_title
    # Keep the bullet on a single line — the AC validator allows multi-line
    # bullets but downstream consumers read them as one logical AC.
    bullet = " ".join(bullet_source.split())
    body = problem.rstrip()
    ac_block = f"## Acceptance criteria\n- {bullet}\n"
    if not body:
        return ac_block
    return f"{body}\n\n{ac_block}"


def _deferred_queue_path(project_root: Path) -> Path:
    return project_root / _DEFERRED_QUEUE_RELATIVE


def _load_deferred_queue(project_root: Path) -> list[DeferredEntry]:
    path = _deferred_queue_path(project_root)
    if not path.is_file():
        return []
    rows: list[DeferredEntry] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(DeferredEntry.model_validate_json(line))
    return rows


def _append_deferred_entry(project_root: Path, entry: DeferredEntry) -> None:
    """Append one entry to the deferred-queue JSONL via atomic write.

    JSONL semantics: append by reading + rewriting atomically. The queue
    is small (operator-curated) so a full-file rewrite is fine.
    """
    rows = _load_deferred_queue(project_root)
    rows.append(entry)
    payload = "\n".join(
        json.dumps(r.model_dump(mode="json"), sort_keys=True) for r in rows
    )
    if payload:
        payload += "\n"
    atomic_write_text(_deferred_queue_path(project_root), payload)


# ---- Coordinator ---------------------------------------------------------


class Coordinator:
    """Dispatch glue between the build plan and the ticket store.

    Bones methods (``materialize_ready_tickets``) are preserved
    untouched. New cycle-aware methods extend the surface for MVP:

    - ``materialize_layer(plan, layer_name)`` — materialize any layer.
    - ``advance_layer_status(plan_path)`` — recompute layer statuses
      from the ticket store and persist deltas.
    - ``next_layer_ready(plan)`` — honor ``OrderingRule`` to surface
      the next layer to materialize.
    - ``dispatch_cycle(plan_path)`` — single cycle: refresh statuses,
      then materialize the next ready layer if any.
    - ``defer_ticket(...)``, ``list_deferred()``, ``triage_deferred(...)``
      — DEFERRED queue helpers.
    """

    def __init__(
        self,
        *,
        tickets: TicketStore,
        project_root: Path,
        emitter: "EventEmitter | None" = None,
    ) -> None:
        self._tickets = tickets
        self._project_root = project_root
        self._emitter = emitter

    # ---- bones-era surface ----------------------------------------------

    async def materialize_ready_tickets(self) -> list[str]:
        """Bones-era helper: materialize every epic's bones layer.

        Kept for backward compatibility with the bones synthetic operator
        + the existing ``MATERIALIZE_TICKETS`` sim step. New cycle-aware
        callers should use ``dispatch_cycle`` instead.
        """
        try:
            plan = load_build_plan(self._project_root)
        except FileNotFoundError:
            return []
        return await self.materialize_layer(plan, LayerName.BONES.value)

    # ---- materialize_layer ----------------------------------------------

    async def materialize_layer(self, plan: BuildPlan, layer_name: str) -> list[str]:
        """Materialize one layer across all epics. Idempotent.

        ``layer_name`` is one of ``"bones" | "mvp" | "final"``; an unknown
        name raises ``ValueError`` so a typo doesn't silently no-op.

        Returns the ticket ids newly created in this call. Tickets that
        already exist are left untouched (Planner-authored tickets in
        MVP; idempotent re-runs).
        """
        if layer_name not in _LAYER_ORDER:
            raise ValueError(
                f"unknown layer {layer_name!r}; expected one of {_LAYER_ORDER!r}"
            )
        layer_enum = LayerName(layer_name)
        created: list[str] = []
        for epic in plan.epics:
            layer_status = self._epic_layer(epic, layer_enum)
            for ticket_id in layer_status.tickets:
                if await self._tickets.get(ticket_id) is not None:
                    continue
                ticket = self._build_ticket(
                    ticket_id=ticket_id,
                    epic=epic,
                    layer=layer_enum,
                )
                await self._tickets.create(ticket)
                created.append(ticket_id)
        return created

    # ---- advance_layer_status ------------------------------------------

    async def advance_layer_status(self, project_root: Path) -> bool:
        """Recompute every epic-layer's status from the ticket store.

        Rules per design §"Iteration":
        - All tickets RESOLVED → ``done`` (empty layer stays NOT_STARTED).
        - Any FAILED → ``blocked``.
        - Any IN_PROGRESS or partial RESOLVED → ``in_progress``.
        - Otherwise (all OPEN, none touched) → unchanged from current.

        Returns True iff any status was changed (and the plan was rewritten).
        """
        try:
            plan = load_build_plan(project_root)
        except FileNotFoundError:
            return False

        any_changed = False
        for epic in plan.epics:
            for layer_enum in (
                LayerName.BONES,
                LayerName.MVP,
                LayerName.FINAL,
            ):
                layer_status = self._epic_layer(epic, layer_enum)
                new_status = await self._compute_layer_status(layer_status)
                if new_status is not None and new_status != layer_status.status:
                    layer_status.status = new_status
                    any_changed = True

        if any_changed:
            plan.last_revised = datetime.now(timezone.utc)
            write_build_plan(project_root, plan)
        return any_changed

    async def _compute_layer_status(self, layer: LayerStatus) -> LayerStatusEnum | None:
        """Compute the layer's status from its tickets' live states.

        Returns ``None`` when the layer has no tickets (caller leaves
        the existing status alone; an empty layer is vacuously
        "not_started" until something gets planned into it).
        """
        if not layer.tickets:
            return None

        statuses: list[TicketStatus] = []
        for ticket_id in layer.tickets:
            t = await self._tickets.get(ticket_id)
            if t is None:
                # Ticket referenced by the plan but not yet materialized
                # — treat as OPEN so the layer reads as not-yet-started.
                statuses.append(TicketStatus.OPEN)
            else:
                statuses.append(t.status)

        if any(s == TicketStatus.FAILED for s in statuses):
            return LayerStatusEnum.BLOCKED
        if all(s == TicketStatus.RESOLVED for s in statuses):
            return LayerStatusEnum.DONE
        # Anything beyond pure-OPEN counts as in_progress (the layer is
        # actively being worked on or partially complete).
        if any(s != TicketStatus.OPEN for s in statuses):
            return LayerStatusEnum.IN_PROGRESS
        return LayerStatusEnum.NOT_STARTED

    # ---- next_layer_ready ----------------------------------------------

    def next_layer_ready(self, plan: BuildPlan) -> str | None:
        """Return the next layer-name to materialize, or None.

        Honors ``OrderingRule``:

        - ``BONES_FIRST`` (default): walk layers in order — return
          ``"bones"`` until *every* epic's bones is done; ``"mvp"`` until
          every epic's mvp is done; ``"final"`` until every epic's final
          is done. An empty layer counts as vacuously done.
        - ``PER_EPIC``: return the next due layer for the first epic
          that's ready to advance.

        Track C Final override: when ``BONES_FIRST`` is in effect AND
        the un-done bones layers all touch modules with
        ``cascade_risk_low=true``, returns ``"mvp"`` instead of
        ``"bones"`` so unblocked epics can advance per
        ``docs/v2.0/pm-workflow/design.md`` §"Bones-first ordering".
        """
        if plan.ordering_rule == OrderingRule.BONES_FIRST:
            for layer_name in _LAYER_ORDER:
                if not self._all_epics_layer_done_or_empty(plan, layer_name):
                    if self._any_epic_layer_has_tickets(plan, layer_name):
                        # Track C Final: cascade_risk_low override.
                        # Only relevant for the bones layer — once we
                        # promote past bones the rest of the chain
                        # follows the normal rule.
                        if (
                            layer_name == LayerName.BONES.value
                            and self._cascade_risk_low_override_allows_mvp(plan)
                        ):
                            return LayerName.MVP.value
                        return layer_name
                    # Every epic's layer is empty AND not_started → vacuous
                    # done; advance to the next layer.
                    continue
            return None

        # PER_EPIC: return the next layer for any epic that's ready.
        for epic in plan.epics:
            for layer_name in _LAYER_ORDER:
                layer = self._epic_layer(epic, LayerName(layer_name))
                if not layer.tickets:
                    continue
                if layer.status != LayerStatusEnum.DONE:
                    return layer_name
        return None

    # ---- cascade_risk_low override (Track C Final) ---------------------

    def _cascade_risk_low_override_allows_mvp(self, plan: BuildPlan) -> bool:
        """True when blocked-bones epics touch only cascade_risk_low modules.

        Per ``docs/v2.0/pm-workflow/design.md`` §"Bones-first ordering":
        the strict default holds unless every still-blocked bones epic
        touches modules the SA flagged ``cascade_risk_low=true``. Then
        the un-blocked epics can promote to MVP without losing the
        integration-validation property bones-first exists to provide
        — the SA's hint says the un-done bones won't reshape what's
        already been built.

        Conditions:
        - At least one epic's bones layer is done (otherwise there's
          nothing to promote).
        - At least one epic's bones layer is NOT done (otherwise we're
          past bones and the override is moot).
        - Every not-done epic's modules ALL have cascade_risk_low=true.
        - At least one un-done epic exists with module references (we
          can't override on epics we know nothing about).

        Architecture-load failure → False (fail closed; we keep the
        strict default rather than over-promoting on a misread).
        """
        try:
            arch = load_architecture(self._project_root)
        except FileNotFoundError:
            return False
        cascade_low_modules = {m.id for m in arch.modules if m.cascade_risk_low}

        bones = LayerName.BONES
        any_done = False
        any_blocked = False
        for epic in plan.epics:
            layer = self._epic_layer(epic, bones)
            if not layer.tickets:
                continue
            if layer.status == LayerStatusEnum.DONE:
                any_done = True
            else:
                any_blocked = True
                # Each blocked epic must touch only cascade_risk_low
                # modules. Empty epic.modules → "we don't know" →
                # treat as not-low-risk so we don't over-promote.
                if not epic.modules:
                    return False
                for module_id in epic.modules:
                    if module_id not in cascade_low_modules:
                        return False
        return any_done and any_blocked

    def _all_epics_layer_done_or_empty(self, plan: BuildPlan, layer_name: str) -> bool:
        """True if every epic's ``layer_name`` is done or empty.

        Empty layers are vacuously satisfied so the bones-first rule
        doesn't deadlock when a single-layer epic has no MVP/final.
        """
        layer_enum = LayerName(layer_name)
        for epic in plan.epics:
            layer = self._epic_layer(epic, layer_enum)
            if not layer.tickets:
                continue  # vacuous-done
            if layer.status != LayerStatusEnum.DONE:
                return False
        return True

    def _any_epic_layer_has_tickets(self, plan: BuildPlan, layer_name: str) -> bool:
        layer_enum = LayerName(layer_name)
        return any(self._epic_layer(epic, layer_enum).tickets for epic in plan.epics)

    # ---- dispatch_cycle ------------------------------------------------

    async def dispatch_cycle(self, project_root: Path) -> CycleResult:
        """One cycle: refresh layer statuses, materialize next layer.

        The Coordinator's mainline tactical operation. Steps:

        1. Refresh layer statuses from the live ticket store.
        2. If a next layer is ready, materialize it.
        3. Return a ``CycleResult`` summarizing both.

        Missing build plan returns an empty result (mirrors
        ``materialize_ready_tickets`` — bones may legitimately not have
        a plan yet).
        """
        result = CycleResult()

        try:
            plan = load_build_plan(project_root)
        except FileNotFoundError:
            return result

        # Snapshot pre-state so we can report what changed.
        pre_statuses: dict[tuple[str, str], LayerStatusEnum] = {}
        for epic in plan.epics:
            for layer_enum in (
                LayerName.BONES,
                LayerName.MVP,
                LayerName.FINAL,
            ):
                pre_statuses[(epic.id, layer_enum.value)] = self._epic_layer(
                    epic, layer_enum
                ).status

        if await self.advance_layer_status(project_root):
            plan = load_build_plan(project_root)
            for epic in plan.epics:
                for layer_enum in (
                    LayerName.BONES,
                    LayerName.MVP,
                    LayerName.FINAL,
                ):
                    new_status = self._epic_layer(epic, layer_enum).status
                    if new_status != pre_statuses[(epic.id, layer_enum.value)]:
                        result.layers_advanced.append(
                            (epic.id, layer_enum.value, new_status.value)
                        )

        next_layer = self.next_layer_ready(plan)
        # Track F Final — when next_layer is ``mvp`` only because the
        # ``cascade_risk_low`` override fired, emit the analytics event
        # so consequences are visible later. We detect this by re-running
        # the override predicate: if it would block bones-first → True
        # AND we're materializing mvp, the override fired.
        cascade_override_fired = (
            next_layer == LayerName.MVP.value
            and plan.ordering_rule == OrderingRule.BONES_FIRST
            and self._cascade_risk_low_override_allows_mvp(plan)
        )
        if next_layer is not None:
            result.tickets_materialized = await self.materialize_layer(plan, next_layer)
            if cascade_override_fired and self._emitter is not None:
                await self._emit_bones_promoted_incomplete_for_cascade(plan)
        result.next_layer = next_layer
        return result

    async def _emit_bones_promoted_incomplete_for_cascade(
        self, plan: BuildPlan
    ) -> None:
        """Emit ``BonesPromotedIncomplete`` for the cascade_risk_low path.

        Captures the full epic context: the epics whose MVP just
        materialized, the still-running bones epics, and the subset
        flagged ``cascade_risk_low=true``.
        """
        from jig.analytics.events import BonesPromotedIncomplete
        from jig.spec_loader import load_architecture

        try:
            arch = load_architecture(self._project_root)
        except FileNotFoundError:
            return

        cascade_low_module_ids = {m.id for m in arch.modules if m.cascade_risk_low}

        promoted: list[str] = []
        still_running: list[str] = []
        sa_low: list[str] = []
        bones = LayerName.BONES
        for epic in plan.epics:
            layer = self._epic_layer(epic, bones)
            if not layer.tickets:
                continue
            if layer.status == LayerStatusEnum.DONE:
                promoted.append(epic.id)
            else:
                still_running.append(epic.id)
                if epic.modules and all(
                    mid in cascade_low_module_ids for mid in epic.modules
                ):
                    sa_low.append(epic.id)

        if not promoted or not still_running:
            return  # not a cascade-override situation

        if self._emitter is not None:
            self._emitter.emit_nowait(
                BonesPromotedIncomplete(
                    promoted_epic_ids=promoted,
                    still_running_bones_epic_ids=still_running,
                    sa_marked_cascade_risk_low=sa_low,
                    operator_rationale_category=(
                        "cascade_risk_low_auto" if sa_low else None
                    ),
                )
            )

    # ---- DEFERRED queue ------------------------------------------------

    async def defer_ticket(self, ticket_id: str, reason: str, notes: str = "") -> None:
        """Add a ticket to the deferred queue + stamp ``deferred_at``.

        Persists to ``.jig/plan/deferred-queue.jsonl`` and updates the
        ticket's ``deferred_at`` field so downstream views can filter.
        """
        now = datetime.now(timezone.utc)
        entry = DeferredEntry(
            ticket_id=ticket_id,
            reason=reason,
            notes=notes,
            deferred_at=now,
        )
        _append_deferred_entry(self._project_root, entry)
        if await self._tickets.get(ticket_id) is not None:
            await self._tickets.update(ticket_id, deferred_at=now)

    def list_deferred(self) -> list[DeferredEntry]:
        """Return the current deferred queue (chronological)."""
        return _load_deferred_queue(self._project_root)

    async def triage_deferred(self, project_root: Path) -> list[TriageDecision]:
        """Mechanical triage pass over the deferred queue.

        For each entry, recommends one of:
        - ``rematerialize`` — the ticket has no critical or important
          unresolved comments (mechanically: it's RESOLVED or CLOSED).
        - ``close`` — the ticket no longer exists in the store.
        - ``leave_deferred`` — default; operator decides next.

        Real LLM-based triage (per design §"DEFERRED queue triage")
        lands in Final; this is the bones+MVP mechanical baseline.

        ``project_root`` is accepted (not used by the mechanical heuristic)
        so the call signature stays stable for the Final upgrade.
        """
        del project_root  # accepted for forward-compat; unused mechanically
        decisions: list[TriageDecision] = []
        for entry in self.list_deferred():
            t = await self._tickets.get(entry.ticket_id)
            if t is None:
                decisions.append(
                    TriageDecision(
                        entry=entry,
                        recommended_action="close",
                        rationale="ticket no longer in store",
                    )
                )
                continue
            if t.status in (TicketStatus.RESOLVED, TicketStatus.CLOSED):
                decisions.append(
                    TriageDecision(
                        entry=entry,
                        recommended_action="rematerialize",
                        rationale=(
                            "ticket already resolved; re-materialize into next layer"
                        ),
                    )
                )
                continue
            decisions.append(
                TriageDecision(
                    entry=entry,
                    recommended_action="leave_deferred",
                    rationale="ticket still in flight",
                )
            )
        return decisions

    # ---- helpers --------------------------------------------------------

    @staticmethod
    def _epic_layer(epic: Epic, layer: LayerName) -> LayerStatus:
        if layer == LayerName.BONES:
            return epic.layers.bones
        if layer == LayerName.MVP:
            return epic.layers.mvp
        return epic.layers.final

    @staticmethod
    def _build_ticket(
        *,
        ticket_id: str,
        epic: Epic,
        layer: LayerName,
    ) -> Ticket:
        """Construct a ``Ticket`` from build-plan epic context.

        Per ``docs/v2.0/pm-workflow/design.md`` §"Ticket structure (extensions)"
        the v2 ticket carries ``epic_id``, ``suite_id``, ``module_id``,
        ``layer``, ``dev_tier``, ``risks_addressed``. Materialized tickets
        get the bones-era defaults (``standard`` dev_tier, single-module
        primary) regardless of layer; Planner-authored tickets in MVP set
        their own fields and won't be overwritten by ``materialize_layer``.

        ``work_type`` is ``FEATURE`` because both bones tracer-bullets and
        MVP feature tickets ship behavior end-to-end.

        **AC synthesis (transitional).** The Ticket model requires an
        Acceptance Criteria section in the description for work-type
        tickets. The current Epic schema (``jig.schemas.plan.Epic``)
        has no ``acceptance_criteria`` field — only ``intent`` — so we
        synthesize a single-bullet AC at materialization time, derived
        from ``epic.intent.problem``. This is a stopgap until the
        Planner-PM workflow is updated to author explicit ACs on each
        Epic (tracked as a follow-on to this validator change).
        """
        module_id = epic.modules[0] if epic.modules else None
        description = _synthesize_description_with_ac(
            problem=epic.intent.problem,
            epic_title=epic.title,
        )
        return Ticket(
            id=ticket_id,
            work_type=WorkType.FEATURE,
            size=Size.M,
            title=f"{epic.title} — {layer.value}",
            description=description,
            created_by=DEFAULT_AUTHOR,
            epic_id=epic.id,
            suite_id=epic.suite,
            module_id=module_id,
            layer=layer.value,
            dev_tier=BONES_DEFAULT_DEV_TIER,
            risks_addressed=list(epic.risks_addressed),
        )
