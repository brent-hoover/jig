---
title: PM Scheduling — Implementation Plan
type: plan
status: active
owner: brent
created: 2026-05-08
updated: 2026-05-08
design: ./design.md
---

# PM Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce merge conflicts from PM over-parallelism via three coordinated changes: `MergeConflictError.conflicted_files`, a `max_parallel` orchestrator cap, and a post-conflict replan spawn — plus PM prompt rules for small projects and bones-first sequencing.

**Architecture:** `MergeConflictError` gains structured file data captured before `merge --abort`. `Project` gets an optional `max_parallel` field; `_start_ready_tickets` checks it before each dispatch. After a successful conflict resolution retry, a fire-and-forget `_try_replan` spawns the PM with REPLAN reason to tighten `depends_on` on pending tickets. The PM prompt gets explicit small-project and bones-first rules.

**Tech Stack:** Python 3.12, pydantic v2, asyncio, pytest, ruff

---

## File map

| File | Change |
|------|--------|
| `jig/worktree.py` | Add `conflicted_files` field to `MergeConflictError`; capture before both aborts |
| `jig/project.py` | Add `max_parallel: int \| None = None` to `Project` |
| `jig/runtime.py` | Add `SpawnReason.REPLAN = "replan"` |
| `jig/prompt_builder.py` | Add `replan_bundle` param; add REPLAN case in `_instructions_section` |
| `jig/agent.py` | Extract `replan_bundle` for REPLAN spawn reason |
| `jig/orchestrator.py` | `_start_ready_tickets` cap; new `_try_replan`; wire replan in `_on_ticket_completed` |
| `jig/defaults/roles/pm.yaml` | Replace "Maximize parallelism" with small-project + bones-first rules |
| `tests/test_worktree.py` | Test `MergeConflictError.conflicted_files` |
| `tests/test_project.py` | Test `max_parallel` round-trips through save/load |
| `tests/test_prompt_builder.py` | Test REPLAN instructions section |
| `tests/test_orchestrator_per_ticket.py` | Test cap, `_try_replan`, and replan wiring |

---

## Task 1: `MergeConflictError.conflicted_files`

**Files:**
- Modify: `jig/worktree.py:21-34` (`MergeConflictError.__init__`)
- Modify: `jig/worktree.py:394-404` (pre-integration conflict handler)
- Modify: `jig/worktree.py:435-444` (final merge conflict handler)
- Test: `tests/test_worktree.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_worktree.py`:

```python
import pytest
from jig.worktree import MergeConflictError


def test_merge_conflict_error_conflicted_files_default_empty() -> None:
    err = MergeConflictError("t1", "jig/t1")
    assert err.conflicted_files == []


def test_merge_conflict_error_conflicted_files_passed_through() -> None:
    err = MergeConflictError("t1", "jig/t1", conflicted_files=["src/a.py", "src/b.py"])
    assert err.conflicted_files == ["src/a.py", "src/b.py"]


def test_merge_conflict_error_none_becomes_empty_list() -> None:
    err = MergeConflictError("t1", "jig/t1", conflicted_files=None)
    assert err.conflicted_files == []
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_worktree.py::test_merge_conflict_error_conflicted_files_default_empty -v
```

Expected: `FAILED` — `MergeConflictError.__init__() got an unexpected keyword argument 'conflicted_files'`

- [ ] **Step 3: Update `MergeConflictError.__init__`**

In `jig/worktree.py`, replace lines 29–34:

```python
    def __init__(
        self,
        ticket_id: str,
        source_branch: str,
        conflicted_files: list[str] | None = None,
    ) -> None:
        self.ticket_id = ticket_id
        self.source_branch = source_branch
        self.conflicted_files: list[str] = conflicted_files or []
        super().__init__(
            f"Merge conflict for {source_branch} (branch preserved for manual merge)"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_worktree.py::test_merge_conflict_error_conflicted_files_default_empty tests/test_worktree.py::test_merge_conflict_error_conflicted_files_passed_through tests/test_worktree.py::test_merge_conflict_error_none_becomes_empty_list -v
```

Expected: `3 passed`

- [ ] **Step 5: Capture conflicted files in the pre-integration conflict handler**

In `jig/worktree.py`, replace the pre-integration `except RuntimeError as exc:` block (lines 394–404):

```python
        except RuntimeError as exc:
            _logger.warning(
                "merge conflict integrating %s into %s — aborting",
                base_branch,
                source_branch,
            )
            try:
                conflicted_raw = await _run_git(
                    worktree, "diff", "--name-only", "--diff-filter=U"
                )
                conflicted = [f for f in conflicted_raw.splitlines() if f]
            except RuntimeError:
                conflicted = []
            try:
                await _run_git(worktree, "merge", "--abort")
            except RuntimeError:
                pass
            raise MergeConflictError(ticket_id, source_branch, conflicted) from exc
```

- [ ] **Step 6: Capture conflicted files in the final merge conflict handler**

In `jig/worktree.py`, replace the final merge `except RuntimeError as exc:` block (lines 435–444):

```python
    except RuntimeError as exc:
        _logger.warning("merge conflict for %s — aborting", ticket_id)
        try:
            conflicted_raw = await _run_git(
                project_path, "diff", "--name-only", "--diff-filter=U"
            )
            conflicted = [f for f in conflicted_raw.splitlines() if f]
        except RuntimeError:
            conflicted = []
        try:
            await _run_git(project_path, "merge", "--abort")
        except RuntimeError:
            await _run_git(project_path, "reset", "--hard", "HEAD")
        raise MergeConflictError(ticket_id, source_branch, conflicted) from exc
```

- [ ] **Step 7: Run full test suite to verify no regressions**

```bash
uv run pytest tests/ -x -q
```

Expected: all tests pass, no errors

- [ ] **Step 8: Commit**

```bash
git add jig/worktree.py tests/test_worktree.py
git commit -m "feat(worktree): add conflicted_files to MergeConflictError"
```

---

## Task 2: `Project.max_parallel`

**Files:**
- Modify: `jig/project.py:47` (after `merge_strategy` field)
- Test: `tests/test_project.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_project.py`:

```python
def test_max_parallel_round_trips(tmp_path: Path) -> None:
    project = Project(
        id="p",
        name="p",
        path=str(tmp_path),
        max_parallel=2,
    )
    save_project(tmp_path, project)
    loaded = load_project(tmp_path)
    assert loaded.max_parallel == 2


def test_max_parallel_defaults_to_none(tmp_path: Path) -> None:
    project = Project(id="p", name="p", path=str(tmp_path))
    save_project(tmp_path, project)
    loaded = load_project(tmp_path)
    assert loaded.max_parallel is None


def test_existing_config_without_max_parallel_loads_as_none(tmp_path: Path) -> None:
    """Projects saved before max_parallel was added deserialize with None."""
    project = Project(id="p", name="p", path=str(tmp_path))
    save_project(tmp_path, project)
    # Strip max_parallel from the YAML to simulate a pre-feature config file
    import yaml
    config_path = tmp_path / ".jig" / "config.yaml"
    raw = yaml.safe_load(config_path.read_text())
    raw.get("project", {}).pop("max_parallel", None)
    config_path.write_text(yaml.safe_dump(raw))
    loaded = load_project(tmp_path)
    assert loaded.max_parallel is None
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_project.py::test_max_parallel_round_trips -v
```

Expected: `FAILED` — `Project() got an unexpected keyword argument 'max_parallel'`

- [ ] **Step 3: Add `max_parallel` to `Project`**

In `jig/project.py`, add after line 46 (`merge_strategy: MergeStrategy = MergeStrategy.SQUASH`):

```python
    max_parallel: int | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_project.py -v
```

Expected: all project tests pass

- [ ] **Step 5: Run full suite**

```bash
uv run pytest tests/ -x -q
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add jig/project.py tests/test_project.py
git commit -m "feat(project): add max_parallel field to Project model"
```

---

## Task 3: `SpawnReason.REPLAN` + prompt builder

**Files:**
- Modify: `jig/runtime.py:24` (after `CONFLICT_RESOLVER`)
- Modify: `jig/prompt_builder.py:176–244` (`_instructions_section`)
- Modify: `jig/prompt_builder.py:467–468` (`build_initial_prompt` signature)
- Modify: `jig/prompt_builder.py:491–497` (`build_initial_prompt` body)
- Modify: `jig/agent.py:224–228` (bundle extraction)
- Test: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_prompt_builder.py`:

```python
def test_replan_instructions_include_conflicted_files() -> None:
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.REPLAN,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        replan_bundle={
            "kind": "replan_spawn",
            "ticket_id": "abc123",
            "conflicted_files": ["src/cli.py", "pyproject.toml"],
        },
    )
    assert "src/cli.py" in prompt
    assert "pyproject.toml" in prompt
    assert "list_tickets" in prompt
    assert "update_ticket" in prompt


def test_replan_instructions_empty_files() -> None:
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.REPLAN,
        ticket=_ticket(),
        parent=None,
        entries=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
        replan_bundle={"kind": "replan_spawn", "conflicted_files": []},
    )
    assert "list_tickets" in prompt
    assert "no specific files recorded" in prompt
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_prompt_builder.py::test_replan_instructions_include_conflicted_files -v
```

Expected: `FAILED` — `build_initial_prompt() got an unexpected keyword argument 'replan_bundle'`

- [ ] **Step 3: Add `SpawnReason.REPLAN`**

In `jig/runtime.py`, add after line 24 (`CONFLICT_RESOLVER = "conflict_resolver"`):

```python
    REPLAN = "replan"
```

- [ ] **Step 4: Add REPLAN case to `_instructions_section`**

In `jig/prompt_builder.py`, update `_instructions_section` signature (line 176) and add the REPLAN case after the `CONFLICT_RESOLVER` block (after line 205):

New signature:
```python
def _instructions_section(
    ticket: Ticket,
    reason: SpawnReason,
    evaluator_bundle: dict[str, Any] | None = None,
    conflict_bundle: dict[str, Any] | None = None,
    replan_bundle: dict[str, Any] | None = None,
    role: str | None = None,
) -> str:
```

Add after the `if reason == SpawnReason.CONFLICT_RESOLVER:` block (after its `return`):

```python
    if reason == SpawnReason.REPLAN:
        conflicted_files = (replan_bundle or {}).get("conflicted_files", [])
        if conflicted_files:
            files_str = "\n".join(f"- `{f}`" for f in conflicted_files)
        else:
            files_str = "- (no specific files recorded)"
        return (
            "## Instructions\n\n"
            f"A merge conflict was auto-resolved for ticket `{ticket.id}`. "
            "The files that conflicted were:\n\n"
            f"{files_str}\n\n"
            "Your job: prevent similar conflicts by serializing pending tickets "
            "likely to touch the same files.\n\n"
            "Steps:\n"
            '1. Use `list_tickets(status="pending")` to list all not-yet-started tickets.\n'
            "2. For each pending ticket likely to touch any of the files above, use "
            '`update_ticket(ticket_id="<id>", depends_on=["<dependency-id>", ...])` '
            "to add ordering constraints.\n"
            "3. Do NOT create or delete tickets. Your only output is `update_ticket` "
            "calls adjusting `depends_on`.\n"
            "4. Exit when done — do not call `update_ticket` on this ticket.\n"
        )
```

- [ ] **Step 5: Add `replan_bundle` to `build_initial_prompt`**

In `jig/prompt_builder.py`, update `build_initial_prompt` signature to add:

```python
    replan_bundle: dict[str, Any] | None = None,
```

And update the `_instructions_section` call to pass it:

```python
        _instructions_section(
            ticket,
            spawn_reason,
            evaluator_bundle=evaluator_bundle,
            conflict_bundle=conflict_bundle,
            replan_bundle=replan_bundle,
            role=role_cfg.role,
        ),
```

- [ ] **Step 6: Extract `replan_bundle` in `agent.py`**

In `jig/agent.py`, add after the `conflict_bundle` extraction (after line 228):

```python
    replan_bundle = (
        ctx.initial_bus_message if ctx.spawn_reason == SpawnReason.REPLAN else None
    )
```

And add `replan_bundle=replan_bundle,` to the `build_initial_prompt` call.

- [ ] **Step 7: Run prompt builder tests**

```bash
uv run pytest tests/test_prompt_builder.py -v
```

Expected: all pass including the two new tests

- [ ] **Step 8: Run full suite**

```bash
uv run pytest tests/ -x -q
```

Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add jig/runtime.py jig/prompt_builder.py jig/agent.py tests/test_prompt_builder.py
git commit -m "feat(orchestrator): add SpawnReason.REPLAN and replan prompt instructions"
```

---

## Task 4: `_start_ready_tickets` `max_parallel` cap

**Files:**
- Modify: `jig/orchestrator.py:1695–1706` (`_start_ready_tickets`)
- Test: `tests/test_orchestrator_per_ticket.py`

- [ ] **Step 1: Write failing test**

Add a new section to `tests/test_orchestrator_per_ticket.py`:

```python
# ---------------------------------------------------------------------------
# C7: _start_ready_tickets respects max_parallel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_ready_tickets_respects_max_parallel(
    tmp_path: Path, monkeypatch
) -> None:
    """_start_ready_tickets stops dispatching when max_parallel running tickets exist."""
    from jig.project import Project, save_project

    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
            max_parallel=1,
        ),
    )
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    gate = asyncio.Event()
    started: list[str] = []

    async def slow_run_agent(ctx, emitter=None):
        started.append(ctx.ticket.id)
        await gate.wait()
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", slow_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    await orch.startup()
    try:
        tid1 = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t1", created_by="user")
        )
        tid2 = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t2", created_by="user")
        )

        await orch._start_ready_tickets()
        # One tick to let the created task register in _running_tickets
        await asyncio.sleep(0)

        assert len(orch._running_tickets) == 1, (
            f"expected 1 running ticket, got {len(orch._running_tickets)}"
        )
        # The capped ticket is still open (not running)
        t2 = await orch.tickets.get(tid2)
        assert t2 is not None
        assert t2.status == TicketStatus.OPEN

        gate.set()  # unblock running ticket so shutdown is clean
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_start_ready_tickets_no_cap_when_max_parallel_none(
    tmp_path: Path, monkeypatch
) -> None:
    """When max_parallel is None (default), all ready tickets are dispatched."""
    _make_project_and_workflow(tmp_path, ["spec"])

    orch = Orchestrator(project_path=tmp_path)

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    gate = asyncio.Event()

    async def slow_run_agent(ctx, emitter=None):
        await gate.wait()
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", slow_run_agent)

    async def fake_ensure(ticket):
        return tmp_path / "worktree"

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    await orch.startup()
    try:
        await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t1", created_by="user")
        )
        await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="t2", created_by="user")
        )

        await orch._start_ready_tickets()
        await asyncio.sleep(0)

        assert len(orch._running_tickets) == 2
        gate.set()
    finally:
        await orch.shutdown()
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_orchestrator_per_ticket.py::test_start_ready_tickets_respects_max_parallel -v
```

Expected: `FAILED` — both tickets dispatched (cap not enforced yet)

- [ ] **Step 3: Add cap to `_start_ready_tickets`**

In `jig/orchestrator.py`, replace `_start_ready_tickets` (lines 1695–1706):

```python
    async def _start_ready_tickets(self) -> None:
        """Find ALL open tickets with satisfied dependencies and start them.

        Respects ``project.max_parallel`` when set — stops dispatching once
        the running-ticket count reaches the cap. Capped tickets stay open
        and are picked up on the next call (triggered by any ticket completion).
        """
        if self.tickets is None:
            return
        ready = await self.tickets.find_ready()
        if not ready:
            _logger.info("no ready tickets in queue")
            return
        for t in ready:
            if (
                self._project is not None
                and self._project.max_parallel is not None
                and len(self._running_tickets) >= self._project.max_parallel
            ):
                _logger.debug(
                    "max_parallel=%d reached; deferring %s",
                    self._project.max_parallel,
                    t.id,
                )
                break
            if t.id not in self._running_tickets:
                _logger.info("picking up ready ticket: %s — %s", t.id, t.title)
                await self._handle_schedule(t.id)
```

- [ ] **Step 4: Run new tests**

```bash
uv run pytest tests/test_orchestrator_per_ticket.py::test_start_ready_tickets_respects_max_parallel tests/test_orchestrator_per_ticket.py::test_start_ready_tickets_no_cap_when_max_parallel_none -v
```

Expected: `2 passed`

- [ ] **Step 5: Run full suite**

```bash
uv run pytest tests/ -x -q
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator_per_ticket.py
git commit -m "feat(orchestrator): enforce max_parallel cap in _start_ready_tickets"
```

---

## Task 5: `_try_replan` + `_on_ticket_completed` wiring

**Files:**
- Modify: `jig/orchestrator.py` — add `_try_replan` method after `_try_resolve_conflict` (after line 1447)
- Modify: `jig/orchestrator.py:1482–1518` — capture `conflicted_files` and call `_try_replan`
- Test: `tests/test_orchestrator_per_ticket.py`

- [ ] **Step 1: Write failing tests**

Add a new section to `tests/test_orchestrator_per_ticket.py`:

```python
# ---------------------------------------------------------------------------
# C8: _try_replan — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_replan_returns_when_role_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_replan logs and returns (does not raise) when the pm role is missing."""
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
        # Must not raise
        await orch._try_replan(tid, ticket, ["src/cli.py"])
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_try_replan_returns_when_agent_raises(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_replan logs and returns (does not raise) when the agent spawn raises."""
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
            lambda *a, **k: RoleConfig(role="pm", phase_prompt="replan"),
        )

        async def boom(ctx, spawned_by="orchestrator"):
            raise RuntimeError("agent exploded")

        orch._run_agent_with_analytics = boom  # type: ignore[method-assign]
        await orch._try_replan(tid, ticket, ["src/cli.py"])
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_try_replan_completes_when_agent_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_replan completes normally when the agent succeeds."""
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
            lambda *a, **k: RoleConfig(role="pm", phase_prompt="replan"),
        )

        async def ok_agent(ctx, spawned_by="orchestrator"):
            return RunAgentResult(status="success", final_text="done")

        orch._run_agent_with_analytics = ok_agent  # type: ignore[method-assign]
        await orch._try_replan(tid, ticket, ["src/cli.py"])
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# C9: _on_ticket_completed fires replan after successful conflict resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replan_fired_after_successful_conflict_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    """When resolver returns True and retry merge succeeds, _try_replan is called
    with the conflicted files from the original exception."""
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

    async def merge_first_conflicts_then_succeeds(
        project_path, ticket_id, base, strategy
    ):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            err = MergeConflictError(ticket_id, f"jig/{ticket_id}")
            err.conflicted_files = ["src/cli.py", "pyproject.toml"]
            raise err
        return f"Merged jig/{ticket_id}"

    monkeypatch.setattr("jig.worktree.merge_ticket", merge_first_conflicts_then_succeeds)
    monkeypatch.setattr("jig.worktree.remove_worktree", lambda *a, **k: None)
    monkeypatch.setattr("jig.worktree.commit_worktree", lambda *a, **k: None)

    async def fake_try_resolve(ticket_id, ticket):
        return True

    orch._try_resolve_conflict = fake_try_resolve  # type: ignore[method-assign]

    replan_calls: list[tuple[str, list[str]]] = []

    async def fake_try_replan(ticket_id, ticket, conflicted_files):
        replan_calls.append((ticket_id, conflicted_files))

    orch._try_replan = fake_try_replan  # type: ignore[method-assign]

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
        assert ticket.status == TicketStatus.RESOLVED
        assert len(replan_calls) == 1
        assert replan_calls[0][0] == tid
        assert "src/cli.py" in replan_calls[0][1]
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_replan_not_fired_when_resolver_fails(
    tmp_path: Path, monkeypatch
) -> None:
    """When the resolver returns False, _try_replan is NOT called."""
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

    async def always_conflict(project_path, ticket_id, base, strategy):
        raise MergeConflictError(ticket_id, f"jig/{ticket_id}")

    monkeypatch.setattr("jig.worktree.merge_ticket", always_conflict)
    monkeypatch.setattr("jig.worktree.remove_worktree", lambda *a, **k: None)

    async def fake_try_resolve(ticket_id, ticket):
        return False

    orch._try_resolve_conflict = fake_try_resolve  # type: ignore[method-assign]

    replan_calls: list[str] = []

    async def fake_try_replan(ticket_id, ticket, conflicted_files):
        replan_calls.append(ticket_id)

    orch._try_replan = fake_try_replan  # type: ignore[method-assign]

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
        assert ticket.status == TicketStatus.MERGE_CONFLICT
        assert replan_calls == []
    finally:
        await orch.shutdown()
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_orchestrator_per_ticket.py::test_try_replan_returns_when_role_missing -v
```

Expected: `FAILED` — `Orchestrator has no attribute '_try_replan'`

- [ ] **Step 3: Add `_try_replan` method to `Orchestrator`**

In `jig/orchestrator.py`, add this method after `_try_resolve_conflict` (after line 1447):

```python
    async def _try_replan(
        self,
        ticket_id: str,
        ticket: "Ticket",
        conflicted_files: list[str],
    ) -> None:
        """Fire-and-forget replan after a successful conflict resolution.

        Spawns the pm role to tighten ``depends_on`` on pending tickets
        that are likely to touch the same files that just conflicted.
        Never raises — any failure is logged and scheduling continues.
        """
        from jig.runtime import AgentSpawnContext, SpawnReason

        if (
            self._project is None
            or self.tickets is None
            or self.threads is None
            or self.memory is None
            or self.bus is None
        ):
            return

        try:
            role_cfg = load_role(self._project_path, "pm")
        except FileNotFoundError:
            _logger.warning(
                "_try_replan: pm role not found; skipping replan for %s", ticket_id
            )
            return

        worktree_path = self._project_path / ".jig" / "worktrees" / ticket_id
        ctx = AgentSpawnContext(
            role="pm",
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.REPLAN,
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
                "kind": "replan_spawn",
                "ticket_id": ticket_id,
                "conflicted_files": conflicted_files,
            },
        )
        try:
            await self._run_agent_with_analytics(ctx, spawned_by="replan")
        except Exception:
            _logger.warning(
                "_try_replan: agent failed for %s", ticket_id, exc_info=True
            )
```

- [ ] **Step 4: Wire `_try_replan` in `_on_ticket_completed`**

In `jig/orchestrator.py`, in the `except MergeConflictError as exc:` block (around line 1482), make these two changes:

First, capture `conflicted_files` immediately after `merge_result = str(exc)`:

```python
            except MergeConflictError as exc:
                merge_result = str(exc)
                conflicted_files = exc.conflicted_files
                _logger.warning(
```

Second, after the successful retry merge log line (after `_logger.info("conflict resolved...")`), add the fire-and-forget replan task:

```python
                        _logger.info(
                            "conflict resolved by agent, merge retry succeeded: %s",
                            merge_result,
                        )
                        asyncio.create_task(
                            self._try_replan(ticket_id, ticket, conflicted_files)
                        )
```

- [ ] **Step 5: Run new tests**

```bash
uv run pytest tests/test_orchestrator_per_ticket.py -k "C8 or C9 or replan" -v
```

Expected: all new tests pass

- [ ] **Step 6: Run full suite**

```bash
uv run pytest tests/ -x -q
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator_per_ticket.py
git commit -m "feat(orchestrator): add _try_replan and wire after successful conflict resolution"
```

---

## Task 6: PM prompt rules

**Files:**
- Modify: `jig/defaults/roles/pm.yaml` — replace "Maximize parallelism" block

No automated tests for prompt content. Verify by reading the YAML after editing.

- [ ] **Step 1: Replace the parallelism guidelines in `pm.yaml`**

In `jig/defaults/roles/pm.yaml`, find and replace the two bullet points starting with `- **Maximize parallelism**` and `- When presenting the dependency graph` with:

```yaml
  - |
    **Parallelism rules** — follow ALL of these:
    1. If you are creating **10 or fewer tickets total**, create a fully
       **linear dependency chain**: each ticket depends on the previous one.
       No parallel fan-out whatsoever on small projects.
    2. On larger projects (> 10 tickets), treat the **first implementation
       ticket** in any group of related features as a sequential gate.
       Other tickets in that group depend on it before fanning out in parallel.
       Never start parallel work until at least one bones/setup ticket has
       been established as the gate.
    3. Beyond the above constraints, minimize unnecessary serialization:
       a ticket should only depend on tickets whose output it truly needs.
  - When presenting the dependency graph, explicitly show which tickets can
    run in parallel, explain why, and confirm that the parallelism rules
    above are satisfied.
```

- [ ] **Step 2: Verify the YAML parses cleanly**

```bash
uv run python -c "
import yaml
from pathlib import Path
txt = Path('jig/defaults/roles/pm.yaml').read_text()
data = yaml.safe_load(txt)
guidelines = data.get('phase_prompt', '')
assert 'linear dependency chain' in guidelines
assert '10 or fewer' in guidelines
assert 'first implementation' in guidelines
print('OK')
"
```

Expected: `OK`

- [ ] **Step 3: Verify `load_role` still loads the role cleanly**

```bash
uv run python -c "
from pathlib import Path
from jig.persistence import load_role
role = load_role(Path('.'), 'pm')
print('role:', role.role)
print('prompt length:', len(role.phase_prompt))
"
```

Expected: prints role name and a non-zero prompt length

- [ ] **Step 4: Run full suite**

```bash
uv run pytest tests/ -x -q
```

Expected: all pass

- [ ] **Step 5: Lint**

```bash
uv run ruff check jig/ && uv run ruff format --check jig/
```

Expected: no issues

- [ ] **Step 6: Commit**

```bash
git add jig/defaults/roles/pm.yaml
git commit -m "feat(pm): add small-project linear chain and bones-first parallelism rules"
```

---

## Final check

- [ ] **Run the full test suite one last time**

```bash
uv run pytest tests/ -q
```

Expected: all tests pass, no failures, no errors.
