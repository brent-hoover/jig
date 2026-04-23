"""Phase 5 Task P — deadlock sweep escalates a blocking question.

Exercises ``jig.deadlock.sweep_blocking_entries`` inside a live
orchestrator: an agent-posted blocking Question sits past the
``escalate_after_s`` threshold; the sweep posts an Escalation
tagged ``any_human`` and flips the ticket to NEEDS_INFO. Uses a
fixed ``now=`` to dial past T2 deterministically — no real wait.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from jig.deadlock import sweep_blocking_entries
from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.thread import Escalation, Question
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._phase5p_helpers import build_orch, poll_until


@pytest.mark.asyncio
async def test_blocking_question_past_t2_escalates_and_flips_needs_info(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
    )
    roles = [RoleConfig(role="spec", phase_prompt="spec")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    async def fake_run_agent(ctx, emitter=None):
        # Post a blocking question targeted at a reviewer role that
        # isn't wired up — the orchestrator's per-ticket loop will
        # park waiting on has_unresolved_blocking.
        await ctx.threads.post(
            Question(
                ticket_id=ctx.ticket.id,
                author="spec",
                target="reviewer",
                question="Should we cache responses?",
                blocking=True,
            )
        )
        return RunAgentResult(status="success", final_text="awaiting answer")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    escalate_after_s = 24 * 3600  # 24h default

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        # Wait for the Question to land.
        async def has_question() -> bool:
            qs = await orch.threads.find_by_kind(tid, "question")
            return any(q.is_blocking() for q in qs)

        assert await poll_until(has_question, timeout_s=3.0), (
            "blocking question never landed on the thread"
        )

        questions = await orch.threads.find_by_kind(tid, "question")
        q = next(q for q in questions if q.is_blocking())
        assert isinstance(q, Question)

        # Sweep with now dialed past T2 (nudge disabled via
        # nudge_after_s=0 so we isolate the T2 escalation).
        future_now = q.created_at + timedelta(seconds=escalate_after_s + 1)
        result = await sweep_blocking_entries(
            tickets=orch.tickets,
            threads=orch.threads,
            bus=orch.bus,
            nudge_after_s=0,
            escalate_after_s=escalate_after_s,
            now=future_now,
        )
        assert q.id in result.escalated, (
            f"question {q.id!r} not in sweep result: {result!r}"
        )

        # An Escalation with responds_to=<question.id>, target=any_human,
        # and reason=deadlock_timeout now exists on the thread.
        # Exactly-one is safe here because the orchestrator runs no
        # background deadlock sweep — ``sweep_blocking_entries`` above
        # is the only caller, and its own idempotency guard prevents
        # double-posting against the same blocking entry.
        entries = await orch.threads.for_ticket(tid)
        escalations = [e for e in entries if isinstance(e, Escalation)]
        assert len(escalations) == 1
        esc = escalations[0]
        assert esc.responds_to == q.id
        assert esc.target == "any_human"
        assert esc.reason == "deadlock_timeout"
        assert esc.author == "orchestrator"

        # Ticket flipped to NEEDS_INFO.
        t = await orch.tickets.get(tid)
        assert t is not None
        assert t.status == TicketStatus.NEEDS_INFO, (
            f"expected NEEDS_INFO, got {t.status!r}"
        )
    finally:
        await orch.shutdown()
