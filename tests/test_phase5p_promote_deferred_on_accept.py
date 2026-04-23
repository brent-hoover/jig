"""Phase 5 Task P — deferred-item promotion on handoff accept.

Covers ``jig.checkpoint_mcp.handle_checkpoint_promote_deferred``
in the full orchestrator loop: a handoff carrying a deferred item
gets promoted to a child ticket before the evaluator accepts.
After accept, the child exists with the right ``parent_id`` and
the DeferredItem flipped to ``status="promoted"``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.checkpoint_mcp import handle_checkpoint_promote_deferred
from jig.models import (
    PhaseConfig,
    RoleConfig,
    SpecificRoleEvaluator,
    WorkflowConfig,
)
from jig.thread import DeferredItem, Handoff
from jig.thread_mcp import handle_thread_accept_handoff, handle_thread_handoff
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._phase5p_helpers import build_orch, poll_until


@pytest.mark.asyncio
async def test_deferred_item_promoted_on_accept_yields_child_ticket(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = WorkflowConfig(
        name="default",
        phases=[
            # Explicit dev evaluator — without it, orchestrator.py:957-958
            # short-circuits and the handoff never gets an evaluator spawn.
            PhaseConfig(
                name="spec",
                role="spec",
                evaluator=SpecificRoleEvaluator(
                    type="specific_role", role="dev"
                ),
            ),
            PhaseConfig(name="dev", role="dev"),
        ],
    )
    roles = [
        RoleConfig(role="spec", phase_prompt="spec"),
        RoleConfig(role="dev", phase_prompt="dev"),
    ]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    from jig.runtime import SpawnReason

    run_calls: list[str] = []

    async def fake_run_agent(ctx, emitter=None):
        run_calls.append(ctx.role)
        if ctx.role == "spec" and ctx.spawn_reason != SpawnReason.EVALUATOR:
            # Route through handle_thread_handoff, not a raw
            # threads.post, so the auto_pre_handoff checkpoint captures
            # the DeferredItem. find_deferred_item searches checkpoints
            # only (jig/store/checkpoints.py::find_deferred_item) — a
            # direct threads.post would leave the item invisible to
            # handle_checkpoint_promote_deferred.
            await handle_thread_handoff(
                tickets=ctx.tickets,
                threads=ctx.threads,
                bus=ctx.bus,
                sender="spec",
                args={
                    "ticket_id": ctx.ticket.id,
                    "phase": "spec",
                    "outputs": ["spec.md"],
                    "summary": "draft",
                    "deferred_items": [
                        DeferredItem(
                            item="add-perf-section",
                            reason="punted out of scope",
                        )
                    ],
                },
                checkpoints=ctx.checkpoints,
            )
        elif ctx.spawn_reason == SpawnReason.EVALUATOR and ctx.role == "dev":
            handoffs = await ctx.threads.find_by_kind(ctx.ticket.id, "handoff")
            pending = next(
                h for h in handoffs
                if isinstance(h, Handoff) and h.acceptance_state == "pending"
            )
            did = pending.deferred_items[0].id
            # Promote first, then accept.
            await handle_checkpoint_promote_deferred(
                tickets=ctx.tickets,
                checkpoints=ctx.checkpoints,
                bus=ctx.bus,
                sender="dev",
                phase_name="spec",
                args={
                    "ticket_id": ctx.ticket.id,
                    "deferred_item_id": did,
                    "title": "add perf section",
                    "work_type": "feature",
                    "description": "promoted from spec deferred list",
                },
                project_path=tmp_path,
            )
            await handle_thread_accept_handoff(
                tickets=ctx.tickets,
                threads=ctx.threads,
                bus=ctx.bus,
                sender="dev",
                args={"handoff_id": pending.id},
                project_path=tmp_path,
                checkpoints=ctx.checkpoints,
            )
        elif ctx.role == "dev":
            # Natural-sequence next-phase spawn (not evaluator).
            await ctx.tickets.update_status(ctx.ticket.id, TicketStatus.RESOLVED)
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        async def resolved() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(resolved, timeout_s=5.0), (
            f"ticket did not resolve; run_calls={run_calls}"
        )

        # A child ticket with parent_id set now exists.
        all_tickets = await orch.tickets.list_all()
        children = [t for t in all_tickets if t.parent_id == tid]
        assert len(children) == 1, (
            f"expected one child ticket, got {[(t.id, t.parent_id) for t in all_tickets]}"
        )
        child = children[0]
        assert child.title == "add perf section"
        assert child.work_type == WorkType.FEATURE

        # The DeferredItem on the auto_pre_handoff checkpoint is now
        # status="promoted" with promoted_ticket_id pointing at child.
        all_cps = await orch.checkpoints.for_ticket(tid, include_historical=True)
        deferred_records = []
        for cp in all_cps:
            for d in cp.deferred:
                deferred_records.append(d)
        promoted = [d for d in deferred_records if d.status == "promoted"]
        assert len(promoted) >= 1, (
            f"expected at least one promoted DeferredItem; got {deferred_records!r}"
        )
        assert any(d.promoted_ticket_id == child.id for d in promoted), (
            f"no promoted item points at child {child.id!r}; got {[(d.item, d.promoted_ticket_id) for d in promoted]}"
        )

        # Handoff landed as accepted.
        handoffs = await orch.threads.find_by_kind(tid, "handoff")
        accepted = [
            h for h in handoffs if isinstance(h, Handoff) and h.acceptance_state == "accepted"
        ]
        assert len(accepted) == 1
        assert accepted[0].accepted_by == "dev"
    finally:
        await orch.shutdown()
