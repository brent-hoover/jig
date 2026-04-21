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
from jig.store.check_results import CheckResultsStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff
from jig.thread_mcp import ThreadError

_logger = logging.getLogger(__name__)


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


__all__ = ["run_handoff_gate"]
