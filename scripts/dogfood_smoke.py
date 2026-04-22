"""Dogfood smoke test: mock-agent orchestrator run on a scratch project.

Verifies the full Orchestrator loop end-to-end without spending real
Claude API calls:
  ticket create  → schedule  → phase 1 runs (mock)  → handoff accepted
  → phase 2 runs (mock)      → merge  → RESOLVED

The mocks replace only:
  * ``jig.orchestrator.run_agent``     — scripted per-role behavior
  * ``jig.worktree.merge_ticket``      — stub merge so RESOLVED is reached
  * ``jig.worktree.remove_worktree``   — no-op cleanup
  * ``Orchestrator._ensure_worktree``  — avoid real git worktree creation

If this script runs green, the Phase 5 F/G/H/I merges haven't regressed
the core orchestration loop. If it fails, the traceback points at the
regression.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.orchestrator import Orchestrator
from jig.persistence import save_role, save_workflow
from jig.project import Project, save_project
from jig.thread import Handoff
from jig.thread_mcp import handle_thread_accept_handoff
from jig.ticket import Ticket, TicketStatus, WorkType

PROJECT = Path("/tmp/jig-smoke-project")


async def main() -> int:
    # Fresh project directory each run.
    if PROJECT.exists():
        shutil.rmtree(PROJECT)
    PROJECT.mkdir()

    # Minimal catalog: two phases, two roles.
    save_project(
        PROJECT,
        Project(
            id="smoke",
            name="smoke",
            path=str(PROJECT),
            language="python",
            package_manager="uv",
        ),
    )
    (PROJECT / ".jig" / "workflows").mkdir(parents=True)
    (PROJECT / ".jig" / "roles").mkdir()
    save_workflow(
        PROJECT,
        WorkflowConfig(
            name="default",
            phases=[
                PhaseConfig(name="spec", role="spec"),
                PhaseConfig(name="dev", role="dev"),
            ],
        ),
    )
    save_role(PROJECT, RoleConfig(role="spec", phase_prompt="spec"))
    save_role(PROJECT, RoleConfig(role="dev", phase_prompt="dev"))

    orch = Orchestrator(project_path=PROJECT)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    run_calls: list[str] = []

    async def fake_run_agent(ctx, emitter=None):
        run_calls.append(ctx.role)
        if ctx.role == "spec":
            # Post a clean (non-blocking) handoff → evaluator approval
            # is still required but nothing objects.
            await ctx.threads.post(
                Handoff(
                    ticket_id=ctx.ticket.id,
                    author="spec",
                    phase="spec",
                    outputs=["spec.md"],
                    summary="draft spec",
                )
            )
        elif ctx.role == "dev":
            # Implementation phase flips the ticket to RESOLVED itself.
            await ctx.tickets.update_status(ctx.ticket.id, TicketStatus.RESOLVED)
        return RunAgentResult(status="success", final_text="ok")

    # Patch the symbol the orchestrator module captured at import time.
    orch_module.run_agent = fake_run_agent

    async def fake_ensure(ticket):
        return PROJECT / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    # Stub all git-touching worktree helpers. We're not testing real git
    # operations here — the orchestrator should advance whether or not
    # auto-commit finds a real repo. commit_worktree has to be patched
    # at its import site inside orchestrator.py (it's imported by name
    # there, so re-binding on jig.worktree won't affect the captured
    # reference).
    import jig.worktree as wt_module
    from jig import orchestrator as orch_module

    async def fake_merge(*args, **kwargs):
        return "stub-merge"

    async def fake_remove(*args, **kwargs):
        return None

    async def fake_commit(*args, **kwargs):
        return "stub-commit-sha"

    wt_module.merge_ticket = fake_merge
    wt_module.remove_worktree = fake_remove
    wt_module.commit_worktree = fake_commit
    orch_module.commit_worktree = fake_commit

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="smoke ticket", created_by="user")
        )
        print(f"[smoke] created ticket {tid}")
        await orch._handle_schedule(tid)

        # Wait for phase 1 (spec) to complete and post its handoff.
        for _ in range(200):
            await asyncio.sleep(0.05)
            handoffs = await orch.threads.find_by_kind(tid, "handoff")
            if handoffs:
                break
        else:
            print("[smoke] FAIL: no handoff from spec phase after 10s")
            return 1
        handoff = handoffs[0]
        print(f"[smoke] spec posted handoff {handoff.id}")

        # Wait for the gate to engage. `has_unresolved_blocking` should
        # show the pending handoff until an evaluator accepts it.
        for _ in range(100):
            await asyncio.sleep(0.05)
            blockers = await orch.threads.has_unresolved_blocking(tid)
            if any(b.kind == "handoff" for b in blockers):
                break
        print(f"[smoke] gate engaged — {len(blockers)} blocker(s)")

        # Evaluator = next phase's role = 'dev'.
        await handle_thread_accept_handoff(
            tickets=orch.tickets,
            threads=orch.threads,
            bus=orch.bus,
            sender="dev",
            args={"handoff_id": handoff.id},
            project_path=PROJECT,
        )
        print("[smoke] handoff accepted")

        # Wait for phase 2 (dev) to run and the ticket to resolve.
        for _ in range(200):
            await asyncio.sleep(0.05)
            ticket = await orch.tickets.get(tid)
            if ticket and ticket.status == TicketStatus.RESOLVED:
                break
        else:
            final = await orch.tickets.get(tid)
            print(
                f"[smoke] FAIL: ticket did not reach RESOLVED in 10s "
                f"(status={final.status if final else 'None'}, calls={run_calls})"
            )
            return 1

        print(f"[smoke] ticket resolved. phases ran: {run_calls}")
        if run_calls != ["spec", "dev"]:
            print(f"[smoke] FAIL: expected ['spec', 'dev'], got {run_calls}")
            return 1
        print("[smoke] PASS")
        return 0
    finally:
        await orch.shutdown()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
