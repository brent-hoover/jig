"""Handoff-gating orchestration primitive (Phase 5 Task O1a).

``jig.check_gate`` is pure logic — given a set of already-recorded
check results, decide pass/fail. ``jig.check_runner`` is pure
execution — given a catalog entry, run it and persist the result.

This module glues them: given a pending ``Handoff`` entry and the
project's workflow + catalog, it

1. Looks up the completing phase and its ``automated_checks``.
2. Runs every declared check through the appropriate runner
   (``ScriptedRunner`` for scripted, ``AgentCheckRunner`` for agent
   checks). Each runner skips checks of the other type, so both can
   be called with the same full name list.
3. Calls ``evaluate_handoff_gate`` to score the latest results and
   emit ``check_failure`` SystemEvents per failing required check.

The function is deliberately side-effecting — it writes CheckResults
and thread events — because that's what the orchestrator's handoff
path needs. Callers that just want "what's the current gate state?"
without running anything should use ``check_gate.check_gate_status``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jig.check_gate import GateVerdict, evaluate_handoff_gate
from jig.check_runner import AgentCheckRunner, ScriptedRunner
from jig.checks import CheckCatalog
from jig.models import WorkflowConfig
from jig.store import Message, MessageBus, MessageType
from jig.store.check_results import CheckResultsStore
from jig.store.checkpoints import CheckpointStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff
from jig.thread_mcp import ThreadError

_logger = logging.getLogger(__name__)

# The "sender" field on bounce / auto-accept bus messages. Matches the
# check runner's author convention so log readers can tell the action
# came from automated gating, not a named evaluator.
_HARNESS_AUTHOR = "harness"
_BOUNCE_AUTHOR = _HARNESS_AUTHOR  # retained for back-compat readers


async def run_handoff_gate(
    *,
    handoff_id: str,
    tickets: TicketStore,
    threads: ThreadStore,
    results: CheckResultsStore,
    catalog: CheckCatalog,
    workflow: WorkflowConfig,
    worktree_path: Path,
    project_path: Path,
) -> GateVerdict:
    """Run the handoff's phase checks and score them through the gate.

    Resolves the completing phase from ``workflow`` using the
    Handoff's ``phase`` field, runs every check listed in
    ``PhaseConfig.automated_checks`` through both runners, then
    returns the gate verdict.

    Raises:
        KeyError: ``handoff_id`` is not in the thread store, or the
            Handoff's phase doesn't appear in ``workflow``.
        ThreadError: ``handoff_id`` resolves to a non-Handoff entry,
            or the Handoff is already resolved (accept/reject). We
            don't re-gate closed handoffs — the orchestrator should
            only call this on pending ones.
    """
    handoff = await threads.get(handoff_id)
    if handoff is None:
        raise KeyError(f"handoff {handoff_id!r} not found")
    if not isinstance(handoff, Handoff):
        raise ThreadError(
            f"entry {handoff_id!r} is a {handoff.kind!r}, not a handoff"
        )
    if handoff.is_resolved():
        raise ThreadError(
            f"handoff {handoff_id!r} is already "
            f"{handoff.acceptance_state!r}; gate only runs on pending"
        )

    ticket = await tickets.get(handoff.ticket_id)
    if ticket is None:
        raise KeyError(f"ticket {handoff.ticket_id} not found")

    phase_cfg = next(
        (p for p in workflow.phases if p.name == handoff.phase), None
    )
    if phase_cfg is None:
        raise KeyError(
            f"phase {handoff.phase!r} not in workflow "
            f"{workflow.name!r}"
        )

    check_names = list(phase_cfg.automated_checks)
    if check_names:
        scripted = ScriptedRunner(
            catalog=catalog,
            results=results,
            worktree_path=worktree_path,
        )
        await scripted.run_for_phase(
            ticket_id=handoff.ticket_id,
            phase=handoff.phase,
            check_names=check_names,
        )

        agent = AgentCheckRunner(
            catalog=catalog,
            results=results,
            worktree_path=worktree_path,
            project_path=project_path,
            threads=threads,
        )
        await agent.run_for_phase(
            ticket=ticket,
            phase=handoff.phase,
            check_names=check_names,
        )

    return await evaluate_handoff_gate(
        catalog=catalog,
        results=results,
        threads=threads,
        ticket_id=handoff.ticket_id,
        phase=handoff.phase,
        required_check_names=check_names,
    )


def _compose_bounce_reason(verdict: GateVerdict) -> str:
    """Build the ``rejection_reason`` body for a bounced handoff.

    Summarizes the gate verdict so an agent reading the thread sees
    what blocked it without having to load every CheckResult.
    """
    lines: list[str] = ["Handoff bounced: required check gate failed."]
    if verdict.failing:
        lines.append("")
        lines.append("Failing required checks:")
        for entry in verdict.failing:
            lines.append(
                f"  - {entry.check_name} ({entry.verdict}) "
                f"[event {entry.event_id}]"
            )
    if verdict.missing:
        lines.append("")
        lines.append(
            "Required checks with no result (treated as fail):"
        )
        for name in verdict.missing:
            lines.append(f"  - {name}")
    lines.append("")
    lines.append(
        "See the thread's check_failure events for full output. "
        "Fix the underlying issues and post a new handoff."
    )
    return "\n".join(lines)


async def bounce_handoff(
    *,
    handoff_id: str,
    threads: ThreadStore,
    bus: MessageBus,
    verdict: GateVerdict,
) -> str:
    """Reject a pending handoff because the check gate blocked it.

    Flips the Handoff's ``acceptance_state`` to ``rejected`` directly
    (bypassing ``handle_thread_reject_handoff``'s evaluator-identity
    guard — the gate is not an evaluator, it's the runner's verdict).
    Publishes ``thread_handoff_rejected`` on the ticket topic so the
    orchestrator's per-ticket loop sees the rejection and reroutes to
    the fix phase, same as a human evaluator's reject would.

    Rejection doesn't prune checkpoints (matches the evaluator-reject
    behavior in ``_close_handoff``) — the retry resumes from the same
    history.

    Returns the rejection reason string as posted. Raises
    ``ThreadError`` if the entry isn't a pending Handoff.
    """
    handoff = await threads.get(handoff_id)
    if handoff is None:
        raise KeyError(f"handoff {handoff_id!r} not found")
    if not isinstance(handoff, Handoff):
        raise ThreadError(
            f"entry {handoff_id!r} is a {handoff.kind!r}, not a handoff"
        )
    if handoff.is_resolved():
        raise ThreadError(
            f"handoff {handoff_id!r} is already "
            f"{handoff.acceptance_state!r}; cannot bounce"
        )
    if verdict.passing:
        raise ThreadError(
            "refusing to bounce a handoff on a passing gate verdict"
        )

    reason = _compose_bounce_reason(verdict)
    await threads.update(
        handoff_id,
        {
            "acceptance_state": "rejected",
            "rejection_reason": reason,
        },
    )

    await bus.publish(
        Message(
            sender=_BOUNCE_AUTHOR,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_handoff_rejected",
                "ticket_id": handoff.ticket_id,
                "handoff_id": handoff_id,
                "phase": handoff.phase,
                "rejection_reason": reason,
                "rejected_by": _BOUNCE_AUTHOR,
                "bounce": True,
                "failing_checks": [
                    f.check_name for f in verdict.failing
                ],
                "missing_checks": list(verdict.missing),
            },
            topic=f"tickets.{handoff.ticket_id}",
        )
    )
    return reason


async def accept_handoff_automated(
    *,
    handoff_id: str,
    threads: ThreadStore,
    bus: MessageBus,
    checkpoints: CheckpointStore | None = None,
) -> None:
    """Accept a pending handoff because its phase uses
    ``evaluator=automated_only`` and the check gate passed.

    Flips the Handoff's ``acceptance_state`` to ``accepted`` with
    ``accepted_by="harness"``, bypassing ``_close_handoff``'s
    ``automated_only manual accept not permitted`` guard. That guard
    exists so a named agent can't side-step the gate by posting an
    accept directly; the orchestrator's gate-driven accept path is
    the only legitimate way in.

    When ``checkpoints`` is provided, the accepted phase's checkpoints
    are marked historical (doc 09 §Phase boundaries), matching the
    human-evaluator accept in ``_close_handoff``.

    Publishes ``thread_handoff_accepted`` with ``auto=True`` on the
    ticket topic so the orchestrator and TUI can react.

    Raises ``ThreadError`` if the entry isn't a pending Handoff, or
    ``KeyError`` if the handoff doesn't exist.
    """
    handoff = await threads.get(handoff_id)
    if handoff is None:
        raise KeyError(f"handoff {handoff_id!r} not found")
    if not isinstance(handoff, Handoff):
        raise ThreadError(
            f"entry {handoff_id!r} is a {handoff.kind!r}, not a handoff"
        )
    if handoff.is_resolved():
        raise ThreadError(
            f"handoff {handoff_id!r} is already "
            f"{handoff.acceptance_state!r}; cannot auto-accept"
        )

    await threads.update(
        handoff_id,
        {
            "acceptance_state": "accepted",
            "accepted_by": _HARNESS_AUTHOR,
        },
    )

    if checkpoints is not None:
        await checkpoints.mark_phase_historical(
            handoff.ticket_id, handoff.phase
        )

    await bus.publish(
        Message(
            sender=_HARNESS_AUTHOR,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "thread_handoff_accepted",
                "ticket_id": handoff.ticket_id,
                "handoff_id": handoff_id,
                "phase": handoff.phase,
                "accepted_by": _HARNESS_AUTHOR,
                "auto": True,
            },
            topic=f"tickets.{handoff.ticket_id}",
        )
    )


__all__ = [
    "accept_handoff_automated",
    "bounce_handoff",
    "run_handoff_gate",
]
