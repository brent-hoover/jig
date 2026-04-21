"""Resolve a phase's ``EvaluatorSpec`` to concrete actor identities.

Called by the handoff guard (``jig/thread_mcp.py``) to decide who is
authorized to accept/reject a Handoff. Also used by the orchestrator
to detect ``automated_only`` phases and by the identity-guard in
doc 10 §Evaluators ("evaluator ≠ completing actor").

The resolver is pure: it takes a spec, the workflow, the phase name,
and the ticket's prior handoff history, and returns a
``ResolvedEvaluator`` describing which actors may evaluate. Resolution
is deterministic — no store reads, no bus calls, no side effects.

Five spec types map to four resolution kinds:

- ``specific_role``, ``previous_phase_role`` → kind ``"role"`` with a
  single-actor list (role name or resolved-identity string).
- ``specific_human`` → kind ``"human"`` with the named identity.
- ``automated_only`` → kind ``"automated"`` with an empty actor list;
  the handoff auto-accepts when all required checks pass (Task D).
- ``multi`` → kind ``"multi"`` with one entry per nested spec. All
  must accept for the handoff to close; any may reject.
- ``None`` spec → the resolver returns ``None`` and the caller falls
  back to the natural-sequence default (next phase's role).

Unresolvable ``previous_phase_role`` — no prior handoff for the named
role — returns ``None`` so the caller can warn-and-allow rather than
deadlock a phase whose prerequisite never ran. (Hard failure would
punish the legitimate case where a project restructures its workflow
mid-flight.)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from jig.models import (
    AutomatedOnlyEvaluator,
    EvaluatorSpec,
    MultiEvaluator,
    PreviousPhaseRoleEvaluator,
    SpecificHumanEvaluator,
    SpecificRoleEvaluator,
    WorkflowConfig,
)
from jig.thread import Handoff


class ResolvedEvaluator(BaseModel):
    """A phase's evaluator spec reduced to acceptance actors.

    ``actors`` is the set of identities a Handoff-accept/reject
    sender must match. For ``kind='multi'``, *every* actor must
    eventually accept; a single reject from any member halts. For
    ``kind='automated'``, the list is empty — the orchestrator
    (Task D) auto-accepts on check pass.
    """

    kind: Literal["role", "human", "automated", "multi"]
    actors: list[str]
    # Per-member resolution for ``multi`` — preserved so the orchestrator
    # can display "awaiting X, Y accepted" even after one member has
    # already acted. Empty for non-multi kinds.
    members: list["ResolvedEvaluator"] = []


def _natural_next_role(
    workflow: WorkflowConfig, phase_name: str
) -> str | None:
    """Return the ``role`` of the phase immediately following
    ``phase_name`` in ``workflow``. ``None`` for a terminal phase or
    an unknown phase name.
    """
    for idx, phase in enumerate(workflow.phases):
        if phase.name != phase_name:
            continue
        nxt = idx + 1
        if nxt < len(workflow.phases):
            return workflow.phases[nxt].role
        return None
    return None


def _resolve_previous_phase_role(
    role: str, handoff_history: list[Handoff]
) -> str | None:
    """Find the actor who most recently accepted a Handoff in ``role``.

    Scans ``handoff_history`` in reverse; returns the ``accepted_by``
    of the latest accepted Handoff whose phase's role is ``role``.
    Returns ``None`` when no such Handoff has been accepted yet —
    the caller interprets that as "fall back."

    The caller supplies the history already filtered to the ticket so
    the resolver stays store-free.
    """
    # Walk newest-first; earliest return wins.
    for h in reversed(handoff_history):
        if h.acceptance_state != "accepted":
            continue
        if h.accepted_by is None:
            continue
        # The phase's role is looked up via the workflow, not the
        # Handoff (the Handoff records phase name only). Callers that
        # need role-matching pass pre-filtered history — see the
        # ``resolve_evaluator`` wrapper which does the lookup.
        return h.accepted_by
    return None


def _resolve_single(
    spec: (
        PreviousPhaseRoleEvaluator
        | SpecificRoleEvaluator
        | AutomatedOnlyEvaluator
        | SpecificHumanEvaluator
    ),
    *,
    workflow: WorkflowConfig,
    handoff_history: list[Handoff],
) -> ResolvedEvaluator | None:
    """Resolve one non-multi spec to a ``ResolvedEvaluator`` or None
    when the spec is unresolvable (e.g., ``previous_phase_role`` with
    no matching history)."""

    if isinstance(spec, SpecificRoleEvaluator):
        return ResolvedEvaluator(kind="role", actors=[spec.role])

    if isinstance(spec, SpecificHumanEvaluator):
        return ResolvedEvaluator(kind="human", actors=[spec.user])

    if isinstance(spec, AutomatedOnlyEvaluator):
        return ResolvedEvaluator(kind="automated", actors=[])

    if isinstance(spec, PreviousPhaseRoleEvaluator):
        phase_roles = {p.name: p.role for p in workflow.phases}
        matching = [
            h
            for h in handoff_history
            if phase_roles.get(h.phase) == spec.role
        ]
        actor = _resolve_previous_phase_role(spec.role, matching)
        if actor is None:
            return None
        return ResolvedEvaluator(kind="role", actors=[actor])

    raise TypeError(f"unknown evaluator spec type: {type(spec).__name__}")


def resolve_evaluator(
    *,
    spec: EvaluatorSpec | None,
    workflow: WorkflowConfig,
    phase_name: str,
    handoff_history: list[Handoff],
) -> ResolvedEvaluator | None:
    """Resolve ``spec`` for ``phase_name`` against the ticket's handoff
    history. Returns ``None`` when the spec is absent or unresolvable
    — caller falls back to the natural-sequence next-phase role via
    ``natural_next_role``.
    """
    if spec is None:
        return None

    if isinstance(spec, MultiEvaluator):
        members: list[ResolvedEvaluator] = []
        actors: list[str] = []
        for sub in spec.evaluators:
            r = _resolve_single(
                sub, workflow=workflow, handoff_history=handoff_history
            )
            if r is None:
                # A single unresolvable nested spec collapses the
                # whole multi — the harness warns and falls back.
                return None
            members.append(r)
            actors.extend(r.actors)
        return ResolvedEvaluator(
            kind="multi", actors=actors, members=members
        )

    return _resolve_single(
        spec, workflow=workflow, handoff_history=handoff_history
    )


def natural_next_role(
    workflow: WorkflowConfig, phase_name: str
) -> str | None:
    """Public re-export of the natural-sequence default so callers
    don't need the private helper."""
    return _natural_next_role(workflow, phase_name)


__all__ = [
    "ResolvedEvaluator",
    "natural_next_role",
    "resolve_evaluator",
]
