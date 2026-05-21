"""Phase 5 Task P — required-check-fails-then-fix E2E.

Exercises:
1. ScriptedRunner executes ``.jig/checks.yaml`` command in worktree.
2. handoff_gate.run_handoff_gate → evaluate_handoff_gate posts
   one check_failure SystemEvent per failing required check.
3. handoff_gate.bounce_handoff flips the handoff to ``rejected``
   with ``rejected_by="harness"`` and the verdict summary.
4. Orchestrator's _phase_handoff_rejected → _route_blocked_phase falls
   back to the most-recent dev phase (the check-failure path has no
   reviewer comments to route on); main loop reruns from that index.
5. On rerun, the worktree state flips the check to passing.
6. AutomatedOnlyEvaluator → accept_handoff_automated auto-closes
   the handoff with ``accepted_by="harness"``.
7. All phases complete → stubbed merge → ticket is RESOLVED.

Workflow rationale: both phases use ``role="dev"`` because the
check-failure path in ``_route_blocked_phase`` falls back to
``_most_recent_phase_with_role(..., "dev")`` and scans only phases
*before* the blocked index. A gated phase needs a prior dev-role
phase to route back to, so ``baseline`` exists purely as the
fix-target. The fake agent disambiguates via ``ctx.phase.name``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.models import (
    AutomatedOnlyEvaluator,
    PhaseConfig,
    RoleConfig,
    WorkflowConfig,
)
from jig.thread import Handoff
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._phase5p_helpers import build_orch, poll_until
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.mark.asyncio
async def test_required_check_fails_then_agent_fixes_and_phase_advances(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = WorkflowConfig(
        name="default",
        phases=[
            # Phase 0 — prior dev phase; the fix-phase target when
            # phase 1 bounces. No checks; auto-accept.
            PhaseConfig(
                name="baseline",
                role="dev",
                evaluator=AutomatedOnlyEvaluator(type="automated_only"),
            ),
            # Phase 1 — the gated phase. Required scripted check.
            PhaseConfig(
                name="gated",
                role="dev",
                automated_checks=["compile"],
                evaluator=AutomatedOnlyEvaluator(type="automated_only"),
            ),
        ],
    )
    roles = [RoleConfig(role="dev", phase_prompt="dev")]
    checks_yaml = (
        "checks:\n"
        "  compile:\n"
        "    type: scripted\n"
        "    command: 'test -f FIXED'\n"
        "    severity: required\n"
    )
    orch = build_orch(
        tmp_path,
        workflow=workflow,
        roles=roles,
        checks_yaml=checks_yaml,
        monkeypatch=monkeypatch,
    )

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    run_calls: list[str] = []  # records ctx.phase.name per invocation
    worktree = tmp_path / "worktree"

    async def fake_run_agent(ctx, emitter=None):
        # ctx.phase is populated for PHASE_PRIMARY spawn reason
        # (the only reason this test triggers). Fall back to role
        # for defensive logging if an evaluator spawn ever sneaks in.
        phase_name = ctx.phase.name if ctx.phase is not None else f"role:{ctx.role}"
        run_calls.append(phase_name)

        if phase_name == "baseline":
            # On the rerun (triggered by gated's bounce), create FIXED
            # so the next gated invocation's check passes. Detect the
            # rerun by checking whether gated has already been invoked.
            if "gated" in run_calls:
                (worktree / "FIXED").write_text("")
            await ctx.threads.post(
                Handoff(
                    ticket_id=ctx.ticket.id,
                    author="dev",
                    phase="baseline",
                    outputs=["baseline.md"],
                    summary=f"baseline attempt {run_calls.count('baseline')}",
                )
            )
        elif phase_name == "gated":
            await ctx.threads.post(
                Handoff(
                    ticket_id=ctx.ticket.id,
                    author="dev",
                    phase="gated",
                    outputs=["gated.md"],
                    summary=f"gated attempt {run_calls.count('gated')}",
                )
            )
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(
                work_type=WorkType.FEATURE,
                title="f",
                created_by="user",
                description=TICKET_AC_PLACEHOLDER,
            )
        )
        await orch._handle_schedule(tid)

        # Wait for terminal RESOLVED via the stubbed-merge path:
        # baseline→auto-accept, gated→bounce, baseline(rerun)→auto-accept,
        # gated(rerun)→auto-accept, all-phases-done → _on_ticket_completed.
        async def resolved() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(resolved, timeout_s=5.0), (
            f"ticket did not resolve; run_calls={run_calls}"
        )

        # Phase ordering: at least baseline → gated → baseline → gated.
        assert run_calls.count("baseline") >= 2, (
            f"expected >=2 baseline runs, got {run_calls}"
        )
        assert run_calls.count("gated") >= 2, (
            f"expected >=2 gated runs, got {run_calls}"
        )
        # Baseline must have run before gated at least once.
        assert run_calls.index("baseline") < run_calls.index("gated"), (
            f"baseline did not run before gated; run_calls={run_calls}"
        )

        # Exactly one harness-bounced handoff (the first gated handoff).
        handoffs = await orch.threads.find_by_kind(tid, "handoff")
        assert len(handoffs) >= 3, f"expected >=3 handoffs, got {len(handoffs)}"
        bounced = [
            h
            for h in handoffs
            if isinstance(h, Handoff)
            and h.acceptance_state == "rejected"
            and (h.rejection_reason or "").startswith("Handoff bounced")
        ]
        assert len(bounced) == 1, (
            f"expected exactly one bounced handoff, got {len(bounced)}"
        )
        assert bounced[0].phase == "gated"

        # All accepted handoffs were auto-accepted by the harness.
        accepted = [
            h
            for h in handoffs
            if isinstance(h, Handoff) and h.acceptance_state == "accepted"
        ]
        assert accepted, "expected at least one accepted handoff"
        assert all(h.accepted_by == "harness" for h in accepted), (
            f"non-harness acceptor present: {[h.accepted_by for h in accepted]}"
        )

        # At least one check_failure SystemEvent landed on the thread,
        # naming the failing required check.
        entries = await orch.threads.for_ticket(tid)
        from jig.thread import SystemEvent  # local import — keeps top-of-file tidy

        failures = [
            e
            for e in entries
            if isinstance(e, SystemEvent) and e.event_type == "check_failure"
        ]
        assert failures, "expected a check_failure SystemEvent after the bounce"
        assert failures[0].check_name == "compile"
    finally:
        await orch.shutdown()
