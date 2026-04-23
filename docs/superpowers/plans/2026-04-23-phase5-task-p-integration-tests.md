# Phase 5 Task P Integration Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four orchestrator-level integration tests covering Phase 5 Task P's tractable scenarios — required-check-fails-then-fix, evaluator=completing-actor conflict, deferred-item promotion to child ticket, and deadlock escalation on a blocking question past T2.

**Architecture:** One file per test under `tests/`, each following the `tests/test_phase4h_e2e.py` pattern: scaffold a project/workflow/roles on `tmp_path`, monkeypatch `jig.orchestrator.run_agent` with a scripted fake driving the exact thread activity needed, stub `_ensure_worktree` + `jig.worktree.merge_ticket` + `jig.worktree.remove_worktree` so the completion path reaches RESOLVED without a real git repo, drive lifecycle with `orch.startup()` → `orch._handle_schedule(tid)` → polling on `tickets.get(tid)` / bus history, then assert final state. A small helper module (`tests/_phase5p_helpers.py`) holds the boilerplate shared across all four files to keep each test tight.

**Tech Stack:** pytest with `asyncio_mode = "auto"`, pydantic v2 models, existing jig fixtures. No new runtime code — Tests 1/3/4 exercise existing Phase 5 wiring; Test 2 exercises the thread-MCP self-cert guard.

**Out of scope (parked — see §Parked below):**
- Black-box agent check cannot read `src/**`
- Dev agent refused at hook boundary for `rm -rf` / `git push --force` / `.jig/spec/**` writes

Both parked tests live at the hook enforcement layer, which runs inside the Claude Code subprocess tool-eval boundary and cannot be driven from pytest. Their compiler-side guarantees (rules.json + `.claude/settings.json` content) are already covered by existing unit tests.

---

## File Structure

| File | Status | Purpose |
|------|--------|---------|
| `tests/_phase5p_helpers.py` | Create | Shared async scaffolding — `build_orch(tmp_path, phases, roles, checks_yaml=None, monkeypatch)` returns a ready-to-drive `Orchestrator` with worktree/merge/remove stubbed. Single factory per test file keeps the test body focused on behavior, not boilerplate. |
| `tests/test_phase5p_check_fail_then_fix.py` | Create | Task 1: required scripted check fails on first handoff → agent re-runs, check now passes → `automated_only` gate auto-accepts → phase advances. |
| `tests/test_phase5p_evaluator_completing_actor.py` | Create | Task 2: phase evaluator resolves to the completing role → fake evaluator agent's `thread_accept_handoff` raises the `evaluator cannot be the completing actor` guard → handoff stays pending, next phase never runs. |
| `tests/test_phase5p_promote_deferred_on_accept.py` | Create | Task 3: completing agent posts a handoff with a `DeferredItem`; evaluator calls `checkpoint_promote_deferred` then `thread_accept_handoff`; a child ticket with `parent_id` exists, the item's `status == "promoted"` and `promoted_ticket_id` points at the child. |
| `tests/test_phase5p_deadlock_escalates_question.py` | Create | Task 4: agent posts a blocking Question, orchestrator waits; a deadlock sweep called with `now = created_at + escalate_after_s + 1` posts an Escalation with `responds_to=<question>.id` and flips the ticket to `NEEDS_INFO`. |

No production code changes. All four tests use existing public surfaces:

- `jig.orchestrator.Orchestrator` + module-level `run_agent` (monkeypatched)
- `jig.project.save_project`, `jig.persistence.save_role`, `jig.persistence.save_workflow`
- `jig.models.PhaseConfig`, `jig.models.WorkflowConfig`, `jig.models.SpecificRoleEvaluator`, `jig.models.AutomatedOnlyEvaluator`
- `jig.ticket.Ticket`, `jig.ticket.TicketStatus`, `jig.ticket.WorkType`
- `jig.thread.Handoff`, `jig.thread.Question`, `jig.thread.DeferredItem`, `jig.thread.Escalation`
- `jig.thread_mcp.handle_thread_accept_handoff`
- `jig.checkpoint_mcp.handle_checkpoint_promote_deferred`
- `jig.deadlock.sweep_blocking_entries`

---

## Task 1: Shared helper — `tests/_phase5p_helpers.py`

**Why first:** All four tests repeat the same five steps — save a `Project`, save a `WorkflowConfig`, save each `RoleConfig`, optionally write `.jig/checks.yaml`, instantiate `Orchestrator` with stubbed worktree/merge/remove. Extracting the factory keeps each test body focused on the behavior under test.

**Files:**
- Create: `tests/_phase5p_helpers.py`

- [ ] **Step 1: Write the helper module**

Create `tests/_phase5p_helpers.py`:

```python
"""Shared scaffolding for Phase 5 Task P integration tests.

Each test configures phases/roles/checks, then calls ``build_orch``
to get an ``Orchestrator`` wired with in-memory stores and stubs
that bypass the real git/worktree paths.
"""

from __future__ import annotations

from pathlib import Path

from jig.models import RoleConfig, WorkflowConfig
from jig.orchestrator import Orchestrator
from jig.persistence import save_role, save_workflow
from jig.project import Project, save_project


def build_orch(
    tmp_path: Path,
    *,
    workflow: WorkflowConfig,
    roles: list[RoleConfig],
    checks_yaml: str | None = None,
    monkeypatch,
) -> Orchestrator:
    """Scaffold a project on ``tmp_path`` and return an
    ``Orchestrator`` with worktree/merge/remove stubbed.

    * ``workflow`` — pre-built ``WorkflowConfig`` (caller chooses
      phases/evaluators so each test controls its scenario).
    * ``roles`` — every role referenced by the workflow. Missing
      role files blow up at evaluator spawn time.
    * ``checks_yaml`` — raw YAML body for ``.jig/checks.yaml``;
      None means no catalog (automated_checks must also be empty).
    * ``monkeypatch`` — pytest fixture; patches are applied on the
      ``jig.worktree`` module for ``merge_ticket`` / ``remove_worktree``.

    The caller is still responsible for ``monkeypatch.setattr(
    orch_module, "run_agent", ...)`` and for calling
    ``orch.startup()`` / ``orch.shutdown()``.
    """
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

    (tmp_path / ".jig" / "workflows").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".jig" / "roles").mkdir(exist_ok=True)
    save_workflow(tmp_path, workflow)
    for role in roles:
        save_role(tmp_path, role)

    if checks_yaml is not None:
        (tmp_path / ".jig" / "checks.yaml").write_text(checks_yaml)

    orch = Orchestrator(project_path=tmp_path)

    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir(exist_ok=True)

    async def fake_ensure(ticket):
        return worktree_dir

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    async def fake_merge(*args, **kwargs):
        return "stub-merge"

    async def fake_remove(*args, **kwargs):
        return None

    monkeypatch.setattr("jig.worktree.merge_ticket", fake_merge)
    monkeypatch.setattr("jig.worktree.remove_worktree", fake_remove)

    return orch


async def poll_until(predicate, *, timeout_s: float = 5.0, step_s: float = 0.05) -> bool:
    """Poll ``predicate`` every ``step_s`` seconds until it returns
    truthy or ``timeout_s`` elapses. Returns the final truthiness —
    callers assert on a specific condition, so ``True`` means the
    condition was met before timeout.
    """
    import asyncio

    steps = max(1, int(timeout_s / step_s))
    for _ in range(steps):
        if await predicate():
            return True
        await asyncio.sleep(step_s)
    return bool(await predicate())
```

- [ ] **Step 2: Verify helper imports clean**

Run: `uv run python -c "from tests import _phase5p_helpers; print(_phase5p_helpers.build_orch.__doc__[:40])"`
Expected: prints the first 40 chars of the docstring, no import errors.

- [ ] **Step 3: Commit**

```bash
git add tests/_phase5p_helpers.py
git commit -m "test: add Phase 5 Task P integration-test scaffolding helper"
```

---

## Task 2: Test — required check fails → agent fixes → advance

**Scenario (doc 10 §Gating + implementation-plan §Task P bullet 1):**

Two-phase workflow, both `role: dev`: `baseline` (no checks, evaluator=automated_only) → `gated` (required scripted check `compile`, evaluator=automated_only). This mirrors production: `_find_fix_phase` (orchestrator.py:1100-1111) only considers `_write_roles = {"dev"}`, scans phases *before* the blocked index, and excludes the blocked phase itself — so the gated phase must have a prior dev-role phase to route back to.

Sequence:

1. `run_agent` fires for phase 0 `baseline`. Fake agent posts a pending Handoff. Gate runs (no checks → pass). Evaluator=automated_only → `accept_handoff_automated` closes it.
2. `run_agent` fires for phase 1 `gated`. Fake agent posts a pending Handoff but has NOT created `FIXED` in the worktree. Gate runs `test -f FIXED` → non-zero → `evaluate_handoff_gate` emits a `check_failure` SystemEvent, `bounce_handoff` flips the Handoff to `rejected`.
3. Orchestrator's `_phase_handoff_rejected` returns True; the main loop treats this as `status="blocked"` and calls `_find_fix_phase(workflow, 1)` → returns 0 (prior dev phase).
4. `run_agent` fires for phase 0 `baseline` AGAIN. Fake agent detects this is a rerun (gated has already been invoked at least once) and `touch`es `FIXED` in the worktree before posting a new Handoff. Auto-accept (no checks).
5. `run_agent` fires for phase 1 `gated` AGAIN. Check passes now. Auto-accept.
6. All phases complete → `_on_ticket_completed` → stubbed merge → ticket status becomes `RESOLVED`.

Assertions cover: handoff bounce+accept transitions with the `"harness"` acceptor string, `check_failure` SystemEvent presence + `check_name`, phase-rerun ordering (`[baseline, gated, baseline, gated]`), and terminal `RESOLVED` status.

**Files:**
- Create: `tests/test_phase5p_check_fail_then_fix.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase5p_check_fail_then_fix.py`:

```python
"""Phase 5 Task P — required-check-fails-then-fix E2E.

Exercises:
1. ScriptedRunner executes ``.jig/checks.yaml`` command in worktree.
2. handoff_gate.run_handoff_gate → evaluate_handoff_gate posts
   one check_failure SystemEvent per failing required check.
3. handoff_gate.bounce_handoff flips the handoff to ``rejected``
   with ``rejected_by="harness"`` and the verdict summary.
4. Orchestrator's _phase_handoff_rejected → _find_fix_phase routes
   back to the prior dev phase; main loop reruns from that index.
5. On rerun, the worktree state flips the check to passing.
6. AutomatedOnlyEvaluator → accept_handoff_automated auto-closes
   the handoff with ``accepted_by="harness"``.
7. All phases complete → stubbed merge → ticket is RESOLVED.

Workflow rationale: both phases use ``role="dev"`` because
``jig.orchestrator._find_fix_phase`` hardcodes
``_write_roles = {"dev"}`` and scans only phases *before* the
blocked index. A gated phase needs a prior dev-role phase to
route back to, so ``baseline`` exists purely as the fix-target.
The fake agent disambiguates via ``ctx.phase.name``.
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
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
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
            f"non-harness acceptor present: "
            f"{[h.accepted_by for h in accepted]}"
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
```

- [ ] **Step 2: Run test, expect failure (imports/paths first)**

Run: `uv run pytest tests/test_phase5p_check_fail_then_fix.py -v`
Expected: fails. The most likely first failure is a `ModuleNotFoundError: No module named 'tests._phase5p_helpers'` if Task 1 wasn't run, or an assertion on ticket resolution if some wiring is off.

- [ ] **Step 3: Debug any real wiring gap**

Likely issues and fixes:
- If `run_calls` stops at `["baseline", "gated"]` and the ticket goes `FAILED` with log `"phase gated blocked but no fix phase found"`: `_find_fix_phase` is returning None. The workflow must have a prior `role="dev"` phase before `gated`. Double-check phase 0 still has `role="dev"`.
- If the first gated handoff never bounces (stays accepted): `.jig/checks.yaml` wasn't written before `startup()`, or the scripted runner's cwd isn't the worktree. Helper writes the file before orchestrator init; verify by printing `(tmp_path / ".jig/checks.yaml").read_text()` inside `fake_run_agent`.
- If `test -f FIXED` passes on the first `gated` run: the fake agent's baseline branch already created `FIXED` because `"gated" in run_calls` was True too early. The guard must only fire on baseline *reruns*, so check that `run_calls.count("baseline") >= 2` would be the alternative detector if ordering is off.
- If `ctx.phase is None` on a recorded invocation: an evaluator spawn is sneaking in (shouldn't happen with automated_only evaluator — it never spawns an agent). Check `ctx.spawn_reason`; the test assumes only `PHASE_PRIMARY` spawns.

No production code changes should be needed — the wiring landed in Task O.

- [ ] **Step 4: Run test, expect pass**

Run: `uv run pytest tests/test_phase5p_check_fail_then_fix.py -v`
Expected: PASS.

- [ ] **Step 5: Run full test suite to confirm no regression**

Run: `uv run pytest tests/ -q`
Expected: no failures. Record the new total count.

- [ ] **Step 6: Commit**

```bash
git add tests/test_phase5p_check_fail_then_fix.py
git commit -m "test(phase5p): required check fails then fix advances phase"
```

---

## Task 3: Test — evaluator=completing-actor conflict blocks advance

**Scenario (doc 10 §Evaluators + doc 16 §Structural guard):**
Phase `spec` has `evaluator = SpecificRoleEvaluator(role="spec")` — the evaluator spec names the same role that completes the phase. After `run_agent(role="spec")` posts a pending Handoff, the orchestrator's gate runs (no checks → pass), resolver returns `kind="role", actors=["spec"]`, and `_spawn_evaluator(role="spec")` launches. Inside that second `run_agent(role="spec")` invocation, the fake agent tries to call `handle_thread_accept_handoff(sender="spec", ...)`. The guard at `jig/thread_mcp.py:1380` raises `ThreadError("evaluator cannot be the completing actor ...")`. The handoff stays pending, the orchestrator never advances to `dev`, and the `ThreadError` is observable via the exception captured by the fake agent.

**Why this is strictly an existing-behavior test:** The accept-time guard is already unit-tested in `tests/test_thread_mcp.py` (lines ~2105, ~2238). This integration test verifies the full orchestrator loop — gate pass → spawn → guard fires → no advance — is consistent with the doc-level promise.

**Files:**
- Create: `tests/test_phase5p_evaluator_completing_actor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase5p_evaluator_completing_actor.py`:

```python
"""Phase 5 Task P — evaluator=completing-actor conflict.

The self-certification guard at thread_mcp.py lines ~1380–1385
raises ``ThreadError("evaluator cannot be the completing actor")``
when an attempted accept's sender equals the handoff author.

This integration test wires a workflow where the phase evaluator
resolves to the same role that completes the phase, so the guard
fires via the full orchestrator → evaluator-spawn → MCP-accept
path. Expected outcome: the handoff stays pending, the next phase
never runs, and the ThreadError is visible to the fake agent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jig.models import (
    PhaseConfig,
    RoleConfig,
    SpecificRoleEvaluator,
    WorkflowConfig,
)
from jig.thread import Handoff
from jig.thread_mcp import ThreadError, handle_thread_accept_handoff
from jig.ticket import Ticket, WorkType
from tests._phase5p_helpers import build_orch, poll_until


@pytest.mark.asyncio
async def test_evaluator_equal_to_completing_role_does_not_advance(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(
                name="spec",
                role="spec",
                evaluator=SpecificRoleEvaluator(type="specific_role", role="spec"),
            ),
            PhaseConfig(name="dev", role="dev"),
        ],
    )
    roles = [
        RoleConfig(role="spec", phase_prompt="spec"),
        RoleConfig(role="dev", phase_prompt="dev"),
    ]
    orch = build_orch(
        tmp_path,
        workflow=workflow,
        roles=roles,
        monkeypatch=monkeypatch,
    )

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    from jig.runtime import SpawnReason

    run_calls: list[str] = []
    accept_errors: list[Exception] = []

    async def fake_run_agent(ctx, emitter=None):
        run_calls.append(ctx.role)
        if ctx.spawn_reason == SpawnReason.EVALUATOR:
            # Dispatch path: evaluator spawn. Pull the handoff id out
            # of the spawn message and try to accept — guard should fire.
            hid: str | None = None
            if ctx.initial_bus_message is not None:
                hid = ctx.initial_bus_message.get("handoff_id")
            if hid is None:
                handoffs = await ctx.threads.find_by_kind(ctx.ticket.id, "handoff")
                hid = handoffs[-1].id if handoffs else None
            assert hid is not None
            try:
                await handle_thread_accept_handoff(
                    tickets=ctx.tickets,
                    threads=ctx.threads,
                    bus=ctx.bus,
                    sender="spec",  # same role as handoff author
                    args={"handoff_id": hid},
                    project_path=tmp_path,
                )
            except ThreadError as e:
                accept_errors.append(e)
        elif ctx.role == "spec":
            # Completing spawn: post the pending handoff authored by "spec".
            await ctx.threads.post(
                Handoff(
                    ticket_id=ctx.ticket.id,
                    author="spec",
                    phase="spec",
                    outputs=["spec.md"],
                    summary="draft",
                )
            )
        elif ctx.role == "dev":
            # Would only run if the guard failed — record it so the
            # assertion below surfaces the cause.
            pytest.fail(
                f"dev should not have run; guard should have held. "
                f"errors seen: {accept_errors!r}"
            )
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        # Wait for the evaluator spawn to fire and record an error.
        async def evaluator_attempted() -> bool:
            return bool(accept_errors)

        assert await poll_until(evaluator_attempted, timeout_s=3.0), (
            f"evaluator never attempted accept; run_calls={run_calls}"
        )

        # Give the orchestrator time to (incorrectly) advance if the
        # guard somehow let the accept through.
        await asyncio.sleep(0.3)

        # Exactly the ThreadError we expect.
        assert any(
            "evaluator cannot be the completing actor" in str(e)
            for e in accept_errors
        ), f"unexpected errors: {accept_errors!r}"

        # Handoff stays pending — no accept landed.
        handoffs = await orch.threads.find_by_kind(tid, "handoff")
        assert len(handoffs) == 1
        assert isinstance(handoffs[0], Handoff)
        assert handoffs[0].acceptance_state == "pending"

        # Dev phase never ran.
        assert "dev" not in run_calls, f"dev should not run; got {run_calls}"
    finally:
        await orch.shutdown()
```

- [ ] **Step 2: Run test, expect pass**

Run: `uv run pytest tests/test_phase5p_evaluator_completing_actor.py -v`
Expected: PASS.

If it fails with "evaluator never attempted accept": verify `_spawn_evaluator` actually spawns for this config. Add a `print(resolved)` line temporarily inside `_run_handoff_gate_if_pending` to confirm `resolved.kind == "role"` and `resolved.actors == ["spec"]`.

If it fails because dev runs anyway: that's a real bug — the guard is silently being bypassed. File a follow-up; the test is correct.

- [ ] **Step 3: Run full suite**

Run: `uv run pytest tests/ -q`
Expected: no regression.

- [ ] **Step 4: Commit**

```bash
git add tests/test_phase5p_evaluator_completing_actor.py
git commit -m "test(phase5p): evaluator equal to completing role blocks advance"
```

---

## Task 4: Test — deferred item promoted to child ticket on accept

**Scenario (doc 09 §Deferred items + implementation-plan Task J):**
Phase `spec` completes with a Handoff carrying `deferred_items=[DeferredItem(item="add-perf-section", reason="punted")]`. The phase declares an explicit `evaluator=SpecificRoleEvaluator(role="dev")` so the orchestrator spawns a `dev`-role evaluator after the gate passes. (A `phase.evaluator=None` leaves the handoff pending indefinitely — `orchestrator.py:957-958` returns early in that case — so an explicit evaluator is required to drive the accept path.) Inside the evaluator spawn, the fake agent calls `handle_checkpoint_promote_deferred(sender="dev", args={"ticket_id": <tid>, "deferred_item_id": <did>})`, which creates a child ticket with `parent_id=<tid>`, flips the DeferredItem's `status="promoted"`, and writes `promoted_ticket_id=<child>`. The evaluator then calls `handle_thread_accept_handoff(sender="dev", ...)` and the phase advances. After the dust settles, the test asserts:

1. A child ticket exists with `parent_id == parent tid`.
2. The underlying `DeferredItem` on the checkpoint has `status == "promoted"` and `promoted_ticket_id == child.id`.
3. The handoff is `accepted`.

**Files:**
- Create: `tests/test_phase5p_promote_deferred_on_accept.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase5p_promote_deferred_on_accept.py`:

```python
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
from jig.thread_mcp import handle_thread_accept_handoff
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
            await ctx.threads.post(
                Handoff(
                    ticket_id=ctx.ticket.id,
                    author="spec",
                    phase="spec",
                    outputs=["spec.md"],
                    summary="draft",
                    deferred_items=[
                        DeferredItem(
                            item="add-perf-section",
                            reason="punted out of scope",
                        )
                    ],
                )
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
```

- [ ] **Step 2: Run test, expect pass**

Run: `uv run pytest tests/test_phase5p_promote_deferred_on_accept.py -v`
Expected: PASS.

If the `promoted` assertion fails: the DeferredItem id may not be stable between the Handoff payload and the checkpoint record. The backfill logic lives in `jig/store/checkpoints.py::_backfill_deferred_ids`; verify the `auto_pre_handoff` checkpoint has the same id the Handoff carries. Temporary debug: print every `DeferredItem.id` across both sources inside the test.

If the child ticket never appears: `handle_checkpoint_promote_deferred` may have hit the idempotent path (`item.promoted_ticket_id is not None`). That would only happen on rerun — assert in the test that `result["created"] is True` (add a captured-result list).

- [ ] **Step 3: Run full suite**

Run: `uv run pytest tests/ -q`
Expected: no regression.

- [ ] **Step 4: Commit**

```bash
git add tests/test_phase5p_promote_deferred_on_accept.py
git commit -m "test(phase5p): deferred item promoted to child ticket on accept"
```

---

## Task 5: Test — blocking question > T2 triggers escalation + needs_info

**Scenario (doc 08 §Deadlock resolution + `jig/deadlock.py`):**
Phase `spec` runs and posts a blocking Question with `target="reviewer"`. The orchestrator's per-ticket loop blocks on `has_unresolved_blocking`. A test-level call to `sweep_blocking_entries(..., now=question.created_at + timedelta(seconds=escalate_after_s + 1), nudge_after_s=0, escalate_after_s=<T2>)` simulates T2 elapsing. The sweep posts an `Escalation` with `responds_to=<question>.id`, `target="any_human"`, and `reason="deadlock_timeout"`, and flips the ticket to `TicketStatus.NEEDS_INFO`. We set `nudge_after_s=0` to isolate the T2 behavior — the T1 nudge logic is already covered by `tests/test_deadlock.py`.

**Files:**
- Create: `tests/test_phase5p_deadlock_escalates_question.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_phase5p_deadlock_escalates_question.py`:

```python
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
```

- [ ] **Step 2: Run test, expect pass**

Run: `uv run pytest tests/test_phase5p_deadlock_escalates_question.py -v`
Expected: PASS.

If the Escalation count is 0: the Question's `is_blocking()` is returning False — check that `blocking=True` was set on the post.

If the Escalation count is 2 or more: the sweep is running twice because the orchestrator's background task also sweeps. The helper doesn't wire a scheduled sweep, so this should only happen if you re-invoke `sweep_blocking_entries` twice. Verify the test body has exactly one call.

- [ ] **Step 3: Run full suite**

Run: `uv run pytest tests/ -q`
Expected: no regression.

- [ ] **Step 4: Commit**

```bash
git add tests/test_phase5p_deadlock_escalates_question.py
git commit -m "test(phase5p): blocking question past T2 escalates and flips needs_info"
```

---

## Task 6: Document the parked tests and check off Task P

**Files:**
- Modify: `docs/implementation-plan.md`

- [ ] **Step 1: Update Task P test checklist**

Find the Task P test list in `docs/implementation-plan.md` (search for "Integration: ticket reaches implement handoff"). Replace the section:

```markdown
- [ ] Integration: ticket reaches implement handoff → required
      check fails → agent fixes → handoff → checks pass →
      evaluator accepts → advance.
- [ ] Integration: evaluator=completing-actor conflict
      → orchestrator escalates, phase doesn't advance.
- [ ] Integration: black-box QA check cannot read `src/**`
      (hook denies; verify via tool-log assertions).
- [ ] Integration: dev agent under default template refused
      at hook level when attempting `rm -rf`, `git push
      --force`, write to `.jig/spec/**`.
- [ ] Integration: deferred item with `status="promoted"`
      becomes a child ticket on handoff accept.
- [ ] Integration: blocking question open for > T2 triggers
      escalation + `needs_info`.
```

With:

```markdown
- [x] Integration: ticket reaches implement handoff → required
      check fails → agent fixes → handoff → checks pass →
      evaluator accepts → advance.
      *`tests/test_phase5p_check_fail_then_fix.py` — wires a
      scripted `test -f FIXED` check into a two-phase workflow,
      first run posts a handoff with FIXED absent (gate bounces),
      second run touches FIXED (gate passes, automated_only
      evaluator auto-accepts), dev phase runs, ticket resolves.*
- [x] Integration: evaluator=completing-actor conflict
      → orchestrator escalates, phase doesn't advance.
      *`tests/test_phase5p_evaluator_completing_actor.py` —
      phase evaluator resolves to the completing role; the
      accept-time self-cert guard at `jig/thread_mcp.py:1380`
      raises `ThreadError("evaluator cannot be the completing
      actor")` inside the evaluator spawn; the handoff stays
      pending and dev never runs. (Pre-spawn orchestrator-level
      escalation noted in §Risks line 1853 is not yet wired; the
      observable outcome — no advance — still matches the exit
      criterion.)*
- [ ] ~~Integration: black-box QA check cannot read `src/**`
      (hook denies; verify via tool-log assertions).~~ **Parked
      — hook-boundary enforcement runs inside Claude Code's
      subprocess tool-eval layer and cannot be driven from
      pytest. Compiler-side guarantees (rules.json shape,
      `.claude/settings.json` content, `black_box_agent.excluded`
      propagation) are covered by `tests/test_capability_compiler.py`
      and `tests/test_checks.py`.**
- [ ] ~~Integration: dev agent under default template refused
      at hook level when attempting `rm -rf`, `git push
      --force`, write to `.jig/spec/**`.~~ **Parked — same
      reason. See `tests/test_capability_compiler.py` for the
      deny-pattern merge + `test_default_role_templates.py` for
      the default dev template's deny list.**
- [x] Integration: deferred item with `status="promoted"`
      becomes a child ticket on handoff accept.
      *`tests/test_phase5p_promote_deferred_on_accept.py` —
      evaluator calls `checkpoint_promote_deferred` before
      accepting the handoff; child ticket is created with
      `parent_id`, DeferredItem flips to `status="promoted"`
      with `promoted_ticket_id` pointing at the child.*
- [x] Integration: blocking question open for > T2 triggers
      escalation + `needs_info`.
      *`tests/test_phase5p_deadlock_escalates_question.py` —
      `sweep_blocking_entries(now=q.created_at + T2 + 1)` with
      `nudge_after_s=0` posts an Escalation tagged `any_human`
      with `reason="deadlock_timeout"` and flips the ticket to
      NEEDS_INFO.*
```

- [ ] **Step 2: Verify the markdown renders**

Run: `grep -A 2 "test_phase5p_" docs/implementation-plan.md | head -40`
Expected: lines referencing all four new test files appear under Task P.

- [ ] **Step 3: Commit**

```bash
git add docs/implementation-plan.md
git commit -m "docs(phase5p): check off integration tests, park hook-boundary cases"
```

---

## Parked

### Test — black-box QA check cannot read `src/**`

**Why parked:** Hook enforcement lives in the Claude Code subprocess at the tool-eval boundary. A pytest process cannot spawn a real Claude Code agent to observe Read/Grep denials; faking the subprocess would test the fake, not the hook.

**What is actually tested instead:**
- `tests/test_capability_compiler.py` — verifies `BlackBoxAgentCheck.excluded` propagates to the compiled `rules.json` deny_patterns.
- `tests/test_checks.py` — round-trips the `black_box_agent` catalog entry and validates the `excluded` list shape.
- Manual smoke: run `jig start` on a project with a black-box QA check, trigger it, and confirm the agent's tool log shows denied Read attempts on excluded paths.

### Test — dev agent refused at hook level for `rm -rf` / force-push / `.jig/spec/**` write

**Why parked:** Same reason — the `PreToolUse` hook only fires inside a real Claude Code run.

**What is actually tested instead:**
- `tests/test_capability_compiler.py` — default dev template's `deny_patterns` include the `rm -rf` and `.jig/spec/**` patterns after compile.
- `tests/test_default_role_templates.py` — the shipped dev template lists these denies at the YAML layer before compile.
- Manual smoke: spawn a dev agent and inspect `.claude/settings.json` for the expected hook entries.

---

## Self-Review Checklist

Ran through this before handoff to implementation:

**Spec coverage:**
- Task P bullet 1 (required-check-fails-then-fix) → Task 2 in this plan.
- Task P bullet 2 (evaluator=completing-actor) → Task 3.
- Task P bullets 3–4 (hook-boundary tests) → Parked section with existing-unit-test pointers.
- Task P bullet 5 (deferred-promoted→child) → Task 4.
- Task P bullet 6 (blocking question > T2) → Task 5.
- Task 6 updates `docs/implementation-plan.md` so the plan's Task P section reflects reality.

**Placeholder scan:** No TBDs, all code blocks complete, all file paths concrete.

**Type consistency:**
- `AutomatedOnlyEvaluator(type="automated_only")` and `SpecificRoleEvaluator(type="specific_role", role=...)` match `jig/models.py` line 66+.
- `handle_checkpoint_promote_deferred` args dict shape matches `jig/checkpoint_mcp.py:256-294`.
- `sweep_blocking_entries(..., now=)` signature matches `jig/deadlock.py:82-90`.
- `handle_thread_accept_handoff` accepts `checkpoints=` kwarg (confirmed in `jig/thread_mcp.py:1401`).
- `Orchestrator.checkpoints` attribute used in Task 4 exists — referenced as `self.checkpoints` throughout `jig/orchestrator.py` (e.g., line 979).
- `AgentSpawnContext.spawn_reason` and `SpawnReason.EVALUATOR` match `jig/runtime.py` / `jig/orchestrator.py:1382`.

**Scope:** One implementation plan, four tests, one doc update. No decomposition needed.
