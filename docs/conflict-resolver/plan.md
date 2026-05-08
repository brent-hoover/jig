---
title: Merge Conflict Auto-Resolver — Implementation Plan
type: plan
status: active
owner: brent
created: 2026-05-07
updated: 2026-05-07
design: ./design.md
---

# Merge Conflict Auto-Resolver — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a ticket branch hits a merge conflict, automatically spawn a `conflict_resolver` agent to fix it and retry the merge instead of routing to MERGE_CONFLICT immediately.

**Architecture:** New `SpawnReason.CONFLICT_RESOLVER` and `conflict_resolver.yaml` role. `base_branch` flows via `initial_bus_message` → `conflict_bundle` → `_instructions_section`, mirroring the EVALUATOR pattern. `_try_resolve_conflict` spawns the agent inside `_on_ticket_completed`; on success the merge retries once; on failure the existing human path runs unchanged.

**Tech Stack:** Python async, pydantic, pytest, ruff. No new dependencies.

---

## File Map

| Action | Path |
|--------|------|
| Create | `jig/defaults/roles/conflict_resolver.yaml` |
| Modify | `jig/runtime.py` |
| Modify | `jig/prompt_builder.py` |
| Modify | `jig/agent.py` |
| Modify | `jig/orchestrator.py` |
| Modify | `tests/test_orchestrator_per_ticket.py` |

---

### Task 1: Add `SpawnReason.CONFLICT_RESOLVER`

**Files:**
- Modify: `jig/runtime.py:15-23`

- [ ] **Open `jig/runtime.py` and add one enum value after `EVALUATOR`:**

```python
class SpawnReason(str, Enum):
    PHASE_PRIMARY = "phase_primary"
    QA_RESPONDER = "qa_responder"
    EVALUATOR = "evaluator"
    CONFLICT_RESOLVER = "conflict_resolver"
```

- [ ] **Run lint to confirm no issues:**

```bash
uv run ruff check jig/runtime.py
```

Expected: no output (clean).

- [ ] **Commit:**

```bash
git add jig/runtime.py
git commit -m "feat(runtime): add SpawnReason.CONFLICT_RESOLVER"
```

---

### Task 2: Create `conflict_resolver.yaml` role

**Files:**
- Create: `jig/defaults/roles/conflict_resolver.yaml`

- [ ] **Create the role file:**

```yaml
role: conflict_resolver
phase_prompt: >
  You are a conflict resolver agent. A merge conflict occurred on this ticket's
  branch and was aborted. Your job is to re-run the merge, resolve all conflict
  markers, and commit the resolution. Read the ticket thread and comments to
  understand the intent behind each side before deciding how to resolve.
allowed_tools:
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - Bash
```

No `allowed_mcps` entry — the jig MCP is provided by default (all roles get it).
No `default_context` — the agent reads ticket context explicitly via MCP tools.
No `allow_add_dependency` — resolving a conflict never needs new packages.

- [ ] **Confirm the file is in the right location:**

```bash
ls jig/defaults/roles/conflict_resolver.yaml
```

Expected: file listed.

- [ ] **Commit:**

```bash
git add jig/defaults/roles/conflict_resolver.yaml
git commit -m "feat(roles): add built-in conflict_resolver role"
```

---

### Task 3: Wire `conflict_bundle` through `prompt_builder.py`

**Files:**
- Modify: `jig/prompt_builder.py`

The `_instructions_section` function (line 176) and `build_initial_prompt` (line ~438) need a `conflict_bundle` parameter, parallel to `evaluator_bundle`.

- [ ] **Add `conflict_bundle` param to `_instructions_section` and add the CONFLICT_RESOLVER case before the `return` for EVALUATOR (line ~191):**

```python
def _instructions_section(
    ticket: Ticket,
    reason: SpawnReason,
    evaluator_bundle: dict[str, Any] | None = None,
    conflict_bundle: dict[str, Any] | None = None,
    role: str | None = None,
) -> str:
    if role in _INIT_ROLES_WITH_OWN_INSTRUCTIONS and reason not in (
        SpawnReason.QA_RESPONDER,
        SpawnReason.EVALUATOR,
        SpawnReason.CONFLICT_RESOLVER,
    ):
        return ""
    if reason == SpawnReason.QA_RESPONDER:
        return (
            "## Instructions\n\n"
            f"Respond to the most recent message on ticket {ticket.id}. "
            "When you've answered, call "
            f'`update_ticket(ticket_id="{ticket.id}", status="resolved")`.\n'
        )
    if reason == SpawnReason.CONFLICT_RESOLVER:
        base_branch = (conflict_bundle or {}).get("base_branch", "develop")
        return (
            "## Instructions\n\n"
            f"A merge conflict occurred while integrating `{base_branch}` into this "
            "ticket's branch. The merge was aborted; the worktree is clean.\n\n"
            "Steps:\n"
            f"1. Run `git merge {base_branch}` to re-introduce the conflict markers.\n"
            "2. Run `git diff --name-only --diff-filter=U` to list conflicted files.\n"
            "3. Use `read_comments` to understand what each side was trying to do.\n"
            "4. For each conflicted file, read it, understand both sides, and resolve.\n"
            "5. Run `git add -A` then `git commit --no-edit` to complete the merge.\n"
            f'6. Call `update_ticket(ticket_id="{ticket.id}", status="resolved")`.\n'
        )
    if reason == SpawnReason.EVALUATOR:
        # ... existing EVALUATOR block unchanged ...
```

- [ ] **Add `conflict_bundle` param to `build_initial_prompt` (line ~452) and pass it through:**

```python
def build_initial_prompt(
    role_cfg: RoleConfig,
    spawn_reason: SpawnReason,
    ticket: Ticket,
    parent: Ticket | None,
    entries: list[ThreadEntry],
    memories: list[str],
    project: Project,
    skills: list[Skill],
    environment_md: str,
    resolved_context: str = "",
    all_roles: list[RoleConfig] | None = None,
    worktree_path: str | None = None,
    phase: PhaseConfig | None = None,
    evaluator_bundle: dict[str, Any] | None = None,
    conflict_bundle: dict[str, Any] | None = None,
) -> str:
```

And in the `_instructions_section` call inside `build_initial_prompt` (line ~475):

```python
        _instructions_section(
            ticket,
            spawn_reason,
            evaluator_bundle=evaluator_bundle,
            conflict_bundle=conflict_bundle,
            role=role_cfg.role,
        ),
```

- [ ] **Run lint:**

```bash
uv run ruff check jig/prompt_builder.py
```

Expected: clean.

- [ ] **Run existing prompt builder tests:**

```bash
uv run pytest tests/test_prompt_builder.py -v -x 2>/dev/null || uv run pytest tests/ -k "prompt" -v -x
```

Expected: all pass.

- [ ] **Commit:**

```bash
git add jig/prompt_builder.py
git commit -m "feat(prompt): add conflict_bundle / CONFLICT_RESOLVER instructions"
```

---

### Task 4: Wire `conflict_bundle` through `agent.py`

**Files:**
- Modify: `jig/agent.py`

`agent.py` extracts `evaluator_bundle` (line ~221) and passes it to `build_initial_prompt`. Add the same for `conflict_bundle`.

- [ ] **Find the `evaluator_bundle` extraction (around line 218-222) and add `conflict_bundle` extraction immediately after:**

```python
    evaluator_bundle = (
        ctx.initial_bus_message if ctx.spawn_reason == SpawnReason.EVALUATOR else None
    )
    conflict_bundle = (
        ctx.initial_bus_message
        if ctx.spawn_reason == SpawnReason.CONFLICT_RESOLVER
        else None
    )
```

- [ ] **Pass `conflict_bundle` to `build_initial_prompt` in the `return build_initial_prompt(...)` call (around line 225):**

```python
    return build_initial_prompt(
        role_cfg=ctx.role_cfg,
        spawn_reason=ctx.spawn_reason,
        ticket=ctx.ticket,
        parent=ctx.parent,
        entries=entries,
        memories=memories,
        project=ctx.project,
        skills=skills,
        environment_md=env_md,
        resolved_context=resolved_context,
        all_roles=all_roles,
        worktree_path=str(ctx.worktree_path),
        phase=ctx.phase,
        evaluator_bundle=evaluator_bundle,
        conflict_bundle=conflict_bundle,
    )
```

- [ ] **Run lint:**

```bash
uv run ruff check jig/agent.py
```

Expected: clean.

- [ ] **Commit:**

```bash
git add jig/agent.py
git commit -m "feat(agent): pass conflict_bundle to build_initial_prompt"
```

---

### Task 5: Write failing tests for `_try_resolve_conflict`

**Files:**
- Modify: `tests/test_orchestrator_per_ticket.py`

Add a new test section after the existing C3 section (around line 289).

- [ ] **Add these three tests at the end of `tests/test_orchestrator_per_ticket.py`:**

```python
# ---------------------------------------------------------------------------
# C5: _try_resolve_conflict — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_resolve_conflict_returns_false_when_role_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_resolve_conflict returns False (not raises) when the
    conflict_resolver role file does not exist."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t", created_by="user")
        )
        ticket = await orch.tickets.get(tid)

        from jig import orchestrator as orch_module

        def raise_not_found(project_path, name):
            raise FileNotFoundError(f"role {name!r} not found")

        monkeypatch.setattr(orch_module, "load_role", raise_not_found)
        result = await orch._try_resolve_conflict(tid, ticket)
        assert result is False
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_try_resolve_conflict_returns_false_when_agent_raises(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_resolve_conflict returns False (not raises) when the agent spawn
    raises an unexpected exception."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t", created_by="user")
        )
        ticket = await orch.tickets.get(tid)

        from jig import orchestrator as orch_module

        monkeypatch.setattr(
            orch_module,
            "load_role",
            lambda *a, **k: RoleConfig(role="conflict_resolver", phase_prompt="x"),
        )

        async def boom(ctx, spawned_by="orchestrator"):
            raise RuntimeError("agent exploded")

        orch._run_agent_with_analytics = boom  # type: ignore[method-assign]
        result = await orch._try_resolve_conflict(tid, ticket)
        assert result is False
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_try_resolve_conflict_returns_true_when_agent_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_resolve_conflict returns True when the agent completes without
    raising."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t", created_by="user")
        )
        ticket = await orch.tickets.get(tid)

        from jig import orchestrator as orch_module
        from jig.agent import RunAgentResult

        monkeypatch.setattr(
            orch_module,
            "load_role",
            lambda *a, **k: RoleConfig(role="conflict_resolver", phase_prompt="x"),
        )

        async def fake_run(ctx, spawned_by="orchestrator"):
            return RunAgentResult(status="success", final_text="done")

        orch._run_agent_with_analytics = fake_run  # type: ignore[method-assign]
        result = await orch._try_resolve_conflict(tid, ticket)
        assert result is True
    finally:
        await orch.shutdown()
```

- [ ] **Run the new tests to confirm they FAIL (method doesn't exist yet):**

```bash
uv run pytest tests/test_orchestrator_per_ticket.py -k "try_resolve" -v
```

Expected: `AttributeError: 'Orchestrator' object has no attribute '_try_resolve_conflict'` or similar FAIL.

---

### Task 6: Implement `_try_resolve_conflict`

**Files:**
- Modify: `jig/orchestrator.py`

Add the method to `Orchestrator`. Place it just before `_on_ticket_completed` (around line 1387). Also add `load_role` to the imports used in `_on_ticket_completed`.

- [ ] **Add `_try_resolve_conflict` to `Orchestrator` (insert before `_on_ticket_completed`):**

```python
    async def _try_resolve_conflict(self, ticket_id: str, ticket) -> bool:
        """Spawn the built-in conflict_resolver agent to fix conflict markers.

        Returns True if the agent completes without exception (caller should
        retry the merge). Returns False on any failure — missing role,
        spawn error, agent crash — so a resolver failure never turns a
        conflict into an orchestrator crash.
        """
        from jig.persistence import load_role
        from jig.runtime import AgentSpawnContext, SpawnReason

        if (
            self._project is None
            or self.tickets is None
            or self.threads is None
            or self.memory is None
            or self.bus is None
        ):
            return False

        try:
            role_cfg = load_role(self._project_path, "conflict_resolver")
        except FileNotFoundError:
            _logger.warning(
                "_try_resolve_conflict: conflict_resolver role not found; "
                "falling back to human resolution for %s",
                ticket_id,
            )
            return False

        worktree_path = self._project_path / ".jig" / "worktrees" / ticket_id
        ctx = AgentSpawnContext(
            role="conflict_resolver",
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.CONFLICT_RESOLVER,
            ticket=ticket,
            parent=None,
            worktree_path=worktree_path,
            project=self._project,
            tickets=self.tickets,
            threads=self.threads,
            memory=self.memory,
            bus=self.bus,
            checkpoints=self.checkpoints,
            initial_bus_message={
                "kind": "conflict_resolve_spawn",
                "ticket_id": ticket_id,
                "base_branch": self._project.default_branch,
            },
        )
        try:
            await self._run_agent_with_analytics(ctx, spawned_by="conflict_resolver")
            return True
        except Exception:
            _logger.warning(
                "_try_resolve_conflict: agent failed for %s",
                ticket_id,
                exc_info=True,
            )
            return False
```

- [ ] **Run the new tests to confirm they PASS:**

```bash
uv run pytest tests/test_orchestrator_per_ticket.py -k "try_resolve" -v
```

Expected: all three pass.

- [ ] **Run lint:**

```bash
uv run ruff check jig/orchestrator.py
```

Expected: clean.

- [ ] **Commit:**

```bash
git add jig/orchestrator.py
git commit -m "feat(orchestrator): add _try_resolve_conflict"
```

---

### Task 7: Write failing test for the updated `_on_ticket_completed` flow

**Files:**
- Modify: `tests/test_orchestrator_per_ticket.py`

Two new tests: resolver succeeds + retry clean → RESOLVED; resolver returns False → MERGE_CONFLICT (unchanged).

Also update the existing `test_merge_conflict_routes_to_merge_conflict_status` to mock `_try_resolve_conflict` returning False — otherwise it tries to spawn the resolver agent.

- [ ] **Update `test_merge_conflict_routes_to_merge_conflict_status` to patch `_try_resolve_conflict`:**

Inside that test (around line 246, after `orch._ensure_worktree = fake_ensure`), add:

```python
    async def fake_try_resolve(ticket_id, ticket):
        return False

    orch._try_resolve_conflict = fake_try_resolve  # type: ignore[method-assign]
```

- [ ] **Add two new tests after the C5 block:**

```python
# ---------------------------------------------------------------------------
# C6: _on_ticket_completed with conflict resolver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_success_routes_to_resolved(
    tmp_path: Path, monkeypatch
) -> None:
    """When _try_resolve_conflict returns True and the retry merge succeeds,
    the ticket reaches RESOLVED."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    from jig.worktree import MergeConflictError

    async def fake_run_agent(ctx, emitter=None):
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    call_count = 0

    async def merge_first_conflicts_then_succeeds(project_path, ticket_id, base, strategy):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise MergeConflictError(ticket_id, f"jig/{ticket_id}")
        return f"Merged jig/{ticket_id}"

    monkeypatch.setattr("jig.worktree.merge_ticket", merge_first_conflicts_then_succeeds)
    monkeypatch.setattr("jig.worktree.remove_worktree", lambda *a, **k: None)
    monkeypatch.setattr("jig.worktree.commit_worktree", lambda *a, **k: None)

    async def fake_try_resolve(ticket_id, ticket):
        return True

    orch._try_resolve_conflict = fake_try_resolve  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)
        running_task = orch._running_tickets.get(tid)
        if running_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(running_task), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

        ticket = await orch.tickets.get(tid)
        assert ticket is not None
        assert ticket.status == TicketStatus.RESOLVED, (
            f"expected RESOLVED, got {ticket.status}"
        )
        assert call_count == 2, f"expected merge_ticket called twice, got {call_count}"
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_resolver_failure_routes_to_merge_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    """When _try_resolve_conflict returns False, the ticket still routes to
    MERGE_CONFLICT (existing human path unchanged)."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    from jig.worktree import MergeConflictError

    async def fake_run_agent(ctx, emitter=None):
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    async def always_conflicts(project_path, ticket_id, base, strategy):
        raise MergeConflictError(ticket_id, f"jig/{ticket_id}")

    monkeypatch.setattr("jig.worktree.merge_ticket", always_conflicts)
    monkeypatch.setattr("jig.worktree.remove_worktree", lambda *a, **k: None)
    monkeypatch.setattr("jig.worktree.commit_worktree", lambda *a, **k: None)

    async def fake_try_resolve(ticket_id, ticket):
        return False

    orch._try_resolve_conflict = fake_try_resolve  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)
        running_task = orch._running_tickets.get(tid)
        if running_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(running_task), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass

        ticket = await orch.tickets.get(tid)
        assert ticket is not None
        assert ticket.status == TicketStatus.MERGE_CONFLICT, (
            f"expected MERGE_CONFLICT, got {ticket.status}"
        )
    finally:
        await orch.shutdown()
```

- [ ] **Run all new C6 tests to confirm they FAIL:**

```bash
uv run pytest tests/test_orchestrator_per_ticket.py -k "resolver" -v
```

Expected: `test_resolver_success_routes_to_resolved` fails (resolver not wired into `_on_ticket_completed` yet). `test_resolver_failure_routes_to_merge_conflict` may pass or fail depending on whether the existing test was updated.

---

### Task 8: Update `_on_ticket_completed` to call the resolver

**Files:**
- Modify: `jig/orchestrator.py`

Modify the `except MergeConflictError` block in `_on_ticket_completed` (around line 1420).

- [ ] **Replace the current `except MergeConflictError` block with:**

```python
            except MergeConflictError as exc:
                merge_result = str(exc)
                _logger.warning(
                    "merge conflict for %s — attempting auto-resolution; "
                    "branch %s preserved",
                    ticket_id,
                    branch_name,
                )
                resolved_by_agent = await self._try_resolve_conflict(ticket_id, ticket)
                if resolved_by_agent:
                    try:
                        merge_result = await merge_ticket(
                            self._project_path,
                            ticket_id,
                            self._project.default_branch,
                            strategy,
                        )
                        _logger.info(
                            "conflict resolved by agent, merge retry succeeded: %s",
                            merge_result,
                        )
                    except MergeConflictError as retry_exc:
                        merge_conflict = True
                        merge_result = str(retry_exc)
                        _logger.warning(
                            "merge conflict persists after agent resolution for %s "
                            "— routing to MERGE_CONFLICT",
                            ticket_id,
                        )
                    except Exception:
                        merge_failed = True
                        merge_result = f"merge retry failed (branch {branch_name} preserved)"
                        _logger.warning(
                            "merge retry failed for %s after conflict resolution",
                            ticket_id,
                            exc_info=True,
                        )
                else:
                    merge_conflict = True
                    _logger.warning(
                        "conflict resolver gave up for %s — routing to MERGE_CONFLICT",
                        ticket_id,
                    )
```

- [ ] **Run all new tests to confirm they PASS:**

```bash
uv run pytest tests/test_orchestrator_per_ticket.py -k "resolver or merge_conflict" -v
```

Expected: all pass, including the updated existing test.

- [ ] **Run lint:**

```bash
uv run ruff check jig/orchestrator.py
```

Expected: clean.

- [ ] **Commit:**

```bash
git add jig/orchestrator.py tests/test_orchestrator_per_ticket.py
git commit -m "feat(orchestrator): auto-resolve merge conflicts via conflict_resolver agent"
```

---

### Task 9: Full suite + final verification

**Files:** none (verification only)

- [ ] **Run the full test suite:**

```bash
uv run pytest tests/ -x -q
```

Expected: all pass (3358+ tests, no failures).

- [ ] **Run lint across all modified modules:**

```bash
uv run ruff check jig/runtime.py jig/prompt_builder.py jig/agent.py jig/orchestrator.py
```

Expected: clean.

- [ ] **Confirm the new role is loadable:**

```bash
uv run python -c "
from pathlib import Path
from jig.persistence import load_role
role = load_role(Path('.'), 'conflict_resolver')
print('role:', role.role)
print('tools:', role.allowed_tools)
"
```

Expected: prints `role: conflict_resolver` and the tool list.

- [ ] **Commit any remaining changes (none expected) or tag the work done.**

```bash
git log --oneline -6
```

Expected: shows the 4 commits from tasks 1, 2, 3+4, 6, and 8.
