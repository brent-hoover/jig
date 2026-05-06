"""MCP tool handlers for the v2 Planner PM (Track F MVP).

The Planner PM consumes upstream PO + SA artifacts and produces the
build plan that the Coordinator dispatches against:

- Reads: ``docs/brief.md``, ``.jig/spec/suites.yaml``,
  ``.jig/spec/suites/<id>/spec.structured.yaml`` per suite,
  ``.jig/spec/architecture.yaml``, ``.jig/spec/modules/<m>/contracts.yaml``
  per module.

- Writes: ``.jig/plan/build-plan.yaml`` (a complete ``BuildPlan``
  organized by epics × bones / MVP / final layers, ordered
  ``bones_first`` by default).

For MVP the Planner is one-shot finalize — accepts a complete
``BuildPlan`` payload, validates, writes atomically, hands off to the
Coordinator. Multi-turn planning conversation is a Final addition.

Distinct from ``coordinator.py`` (the dispatch glue that materializes
the plan into ticket-store rows; bones still uses it). The synthetic
operator's ``write_build_plan`` helper in ``spec_loader.py`` stays
intact so existing scenarios that hand-write a plan keep working —
the Planner is the *agent* path; ``write_build_plan`` is the
*operator* path.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jig.handoff_resolve import resolve_after_handoff
from jig.schemas.plan import BuildPlan, LayerName
from jig.spec_loader import write_build_plan
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff

# The Planner PM works on a project-scoped ticket — one plan per
# project, like SA's ``"architecture"`` ticket. ``"plan"`` mirrors
# the build-plan artifact name so /init and TUI status panes can
# reason about the ticket without translation.
PLANNER_TICKET_ID = "plan"

# Coordinator picks up after the Planner. F-MVP follow-on lands the
# continuous Coordinator agent; for now this captures the next-phase
# string so wiring hangs together when the dispatch loop arrives.
PLANNER_NEXT_PHASE = "coordinator"


# ---- coercion -------------------------------------------------------------


def _coerce_build_plan(raw: Any) -> BuildPlan:
    """Validate ``raw`` against the ``BuildPlan`` schema.

    Accepts the model instance directly (idempotent) or a dict from
    the MCP tool boundary. Re-raised as ``ValueError`` so the agent
    sees a uniform error type alongside the other validation
    rejections in ``handle_plan_finalize``.
    """
    if isinstance(raw, BuildPlan):
        return raw
    if not isinstance(raw, dict):
        raise ValueError(
            f"plan must be a dict or BuildPlan, got {type(raw).__name__}"
        )
    try:
        return BuildPlan.model_validate(raw)
    except ValidationError as e:
        # Pydantic raises a uniform error message that includes the
        # path to the offending field. Surfacing it verbatim gives the
        # agent enough signal to fix the input without us guessing
        # which validation tripped.
        raise ValueError(f"plan does not validate: {e}") from e


# ---- plan-shape validation ----------------------------------------------


def _validate_unique_ticket_ids(plan: BuildPlan) -> None:
    """Reject any ticket id that appears more than once in the plan.

    The plan's ``tickets`` lists reference the live ``TicketStore``
    by id; a duplicate id means two epics (or two layers within one
    epic) would claim ownership of the same row, which silently
    breaks dispatch ordering. We catch it at finalize time so the
    Coordinator never sees an ambiguous plan.
    """
    seen: list[str] = []
    for epic in plan.epics:
        for layer in (
            epic.layers.bones,
            epic.layers.mvp,
            epic.layers.final,
        ):
            seen.extend(layer.tickets)
    counts = Counter(seen)
    duplicates = sorted(tid for tid, n in counts.items() if n > 1)
    if duplicates:
        raise ValueError(
            f"ticket id(s) {duplicates!r} listed more than once across "
            "the plan; each ticket must appear in exactly one (epic, layer)"
        )


def _validate_some_bones_layer_populated(plan: BuildPlan) -> None:
    """Reject a plan whose every epic's bones layer is empty.

    ``ordering_rule: bones_first`` means MVP layer of any epic gates
    on the bones layer of every epic. With no bones tickets at all
    the rule has nothing to dispatch first, leaving the Coordinator
    sitting on an empty cycle. Catch it here rather than letting
    dispatch silently no-op.
    """
    if not any(epic.layers.bones.tickets for epic in plan.epics):
        raise ValueError(
            "build plan has no bones-layer tickets in any epic; "
            "bones_first ordering requires at least one bones ticket "
            "to dispatch first"
        )


# ---- the finalize handler -------------------------------------------------


async def handle_plan_finalize(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    plan: Any,
    author: str,
) -> str:
    """One-shot Planner finalize — write build-plan.yaml + hand off to coordinator.

    Returns the Handoff entry id. Raises ``ValueError`` when:

    - ``plan`` doesn't validate against the ``BuildPlan`` schema
      (e.g. an epic missing ``intent`` — Pydantic enforces it; we
      surface the error verbatim).
    - any ticket id appears more than once across the plan
      (cross-epic or cross-layer duplicate).
    - every epic's bones layer is empty (``bones_first`` ordering
      would have nothing to dispatch first).

    Writes are atomic: ``build-plan.yaml`` replaces on success,
    untouched on validation failure.
    """
    parsed = _coerce_build_plan(plan)
    _validate_unique_ticket_ids(parsed)
    _validate_some_bones_layer_populated(parsed)

    write_build_plan(project_path, parsed)

    handoff = Handoff(
        ticket_id=PLANNER_TICKET_ID,
        author=author,
        phase=PLANNER_NEXT_PHASE,
        outputs=[".jig/plan/build-plan.yaml"],
        summary=(
            f"Planner: {len(parsed.epics)} epic(s), "
            f"{sum(len(e.layers.bones.tickets) for e in parsed.epics)} "
            "bones ticket(s)"
        ),
    )
    entry_id = await threads.post(handoff)
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "handoff_posted",
                "ticket_id": PLANNER_TICKET_ID,
                "phase": PLANNER_NEXT_PHASE,
                "epic_count": len(parsed.epics),
            },
            topic="orchestrator",
        )
    )
    await resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id=PLANNER_TICKET_ID,
        author=author,
    )
    return entry_id


__all__ = [
    "LayerName",  # re-export for convenience
    "PLANNER_NEXT_PHASE",
    "PLANNER_TICKET_ID",
    "handle_plan_finalize",
]
