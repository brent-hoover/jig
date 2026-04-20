"""Phase 4 Task H end-to-end test.

Exercises the full gating path at the orchestrator level:

1. Phase 1 (``spec``) runs and posts a blocking ``Objection`` plus a
   pending ``Handoff`` carrying a ``DeferredItem``. The fake agent
   then returns ``status="success"``.
2. The orchestrator's Task H gate sees two unresolved blocking
   entries and refuses to advance — ``phase_blocked_by_thread`` is
   published on the orchestrator topic.
3. External helpers post a ``Resolution`` and ``thread_accept_resolution``
   (objector-only). The gate still blocks: the ``Handoff`` is pending.
4. Evaluator (``dev``, the next phase's role) calls
   ``thread_accept_handoff``.
5. The gate unblocks, the orchestrator advances to phase 2, and the
   fake ``dev`` agent sees the handoff's deferred item via the
   thread store — i.e., the evaluator's view surfaces the item.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.thread import DeferredItem, Handoff, Objection
from jig.thread_mcp import (
    handle_thread_accept_handoff,
    handle_thread_accept_resolution,
    handle_thread_resolve_objection,
)
from jig.ticket import Ticket, TicketStatus, WorkType


@pytest.mark.asyncio
async def test_blocking_objection_resolves_then_advances_with_deferred_item(
    tmp_path: Path, monkeypatch
) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )
    wf = WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(name="spec", role="spec"),
            PhaseConfig(name="dev", role="dev"),
        ],
    )
    from jig.persistence import save_role, save_workflow

    (tmp_path / ".jig" / "workflows").mkdir(parents=True)
    (tmp_path / ".jig" / "roles").mkdir()
    save_workflow(tmp_path, wf)
    save_role(tmp_path, RoleConfig(role="spec", phase_prompt="spec"))
    save_role(tmp_path, RoleConfig(role="dev", phase_prompt="dev"))

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    run_calls: list[str] = []
    observed_deferred: list[DeferredItem] = []

    async def fake_run_agent(ctx, emitter=None):
        run_calls.append(ctx.role)
        if ctx.role == "spec":
            # Blocking Objection authored by a reviewer — only the
            # reviewer can accept a later Resolution.
            await ctx.threads.post(
                Objection(
                    ticket_id=ctx.ticket.id,
                    author="reviewer",
                    target_artifact="spec.md",
                    text="missing acceptance criteria",
                )
            )
            # Pending Handoff with a deferred item that the evaluator
            # will see when the gate unblocks.
            await ctx.threads.post(
                Handoff(
                    ticket_id=ctx.ticket.id,
                    author="spec",
                    phase="spec",
                    outputs=["spec.md"],
                    summary="draft spec",
                    deferred_items=[
                        DeferredItem(
                            item="add-perf-section",
                            reason="punted out of scope",
                        )
                    ],
                )
            )
        elif ctx.role == "dev":
            # The evaluator's view: deferred items from the prior phase
            # Handoff are readable via the thread store.
            handoffs = await ctx.threads.find_by_kind(
                ctx.ticket.id, "handoff"
            )
            for h in handoffs:
                assert isinstance(h, Handoff)
                observed_deferred.extend(h.deferred_items)
            await ctx.tickets.update_status(
                ctx.ticket.id, TicketStatus.RESOLVED
            )
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(
                work_type=WorkType.FEATURE, title="f", created_by="user"
            )
        )
        await orch._handle_schedule(tid)

        # Wait for phase 1 to complete and the gate to engage.
        for _ in range(100):
            await asyncio.sleep(0.05)
            blockers = await orch.threads.has_unresolved_blocking(tid)
            if len(blockers) >= 2:
                break
        assert run_calls == ["spec"], (
            f"expected only 'spec' run, got {run_calls}"
        )

        entries = await orch.threads.for_ticket(tid)
        objection = next(e for e in entries if e.kind == "objection")
        handoff = next(e for e in entries if e.kind == "handoff")

        # Orchestrator should have broadcast phase_blocked_by_thread.
        msgs = await orch.bus.get_history("orchestrator")
        assert any(
            m.payload.get("kind") == "phase_blocked_by_thread"
            and m.payload.get("ticket_id") == tid
            for m in msgs
        ), "expected phase_blocked_by_thread on orchestrator topic"

        # Resolve the Objection: Resolution + objector-only accept.
        await handle_thread_resolve_objection(
            threads=orch.threads,
            bus=orch.bus,
            sender="dev",
            args={
                "objection_id": objection.id,
                "text": "added acceptance criteria",
            },
        )
        await handle_thread_accept_resolution(
            threads=orch.threads,
            bus=orch.bus,
            sender="reviewer",
            args={"objection_id": objection.id},
        )

        # The Handoff is still pending — gate still blocks.
        # Give the orchestrator a moment to re-check and NOT advance.
        await asyncio.sleep(0.3)
        assert "dev" not in run_calls, (
            f"dev phase ran despite pending Handoff: {run_calls}"
        )
        still_blocking = await orch.threads.has_unresolved_blocking(tid)
        assert [b.kind for b in still_blocking] == ["handoff"]

        # Evaluator accepts the Handoff. The next phase's role ("dev")
        # resolves as the implicit evaluator.
        await handle_thread_accept_handoff(
            tickets=orch.tickets,
            threads=orch.threads,
            bus=orch.bus,
            sender="dev",
            args={"handoff_id": handoff.id},
            project_path=tmp_path,
        )

        # Orchestrator advances to phase 2 and resolves the ticket.
        for _ in range(100):
            await asyncio.sleep(0.05)
            current = await orch.tickets.get(tid)
            if current and current.status == TicketStatus.RESOLVED:
                break

        assert run_calls == ["spec", "dev"]
        final = await orch.tickets.get(tid)
        assert final is not None
        assert final.status == TicketStatus.RESOLVED
        assert [d.item for d in observed_deferred] == ["add-perf-section"]
    finally:
        await orch.shutdown()
