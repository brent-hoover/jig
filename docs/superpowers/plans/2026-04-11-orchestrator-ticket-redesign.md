# Orchestrator + Ticket Model Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Jig's Issue/Task/AgentInstance/AgentPool/BusMonitor quartet with a unified `Ticket` + `Comment` model and a singleton Orchestrator that runs service, per-ticket, and dispatch loops in one object. Agents become ephemeral, inter-agent messaging flows through streaming input, and environment context is injected via structured fields + jig skills + `environment.md`.

**Architecture:** Ticket/Comment collections backed by `TypedCollection` over the existing `JsonlStore`. Orchestrator holds `_running_tickets` + `_live_subscribers` tables and dispatches agents off bus events. `run_agent` uses the Claude Agent SDK streaming-input mode (`query(prompt=async_iterable)`) to feed incoming bus messages as conversation turns. One worktree per top-level ticket. Jig-shipped markdown skills under `jig/skills/` are discovered via `importlib.resources` and matched against `Project` fields via YAML frontmatter.

**Tech Stack:** Python 3.12+, `claude-agent-sdk` streaming input, `pydantic` v2, `asyncio`, `importlib.resources`, `pytest`, `uv`. Existing `jig/store/` library (`JsonlStore`, `TypedCollection`, `MessageBus`) is reused and not rewritten.

**Spec:** `docs/superpowers/specs/2026-04-11-orchestrator-ticket-redesign-design.md`

---

## Phasing note

This is one plan with nine phases. Each phase ends at a testable state; merge phases as cohesive chunks rather than individual tasks. Phases 0–3 stand on their own (models, persistence, MCP tools, environment context) and can be reviewed in isolation. Phases 4–6 (agent runtime, orchestrator, worktree) are interlocked and should land together. Phases 7–9 (TUI wiring, deletion, test rewrite cleanup) come last because they depend on the new runtime being alive.

Every phase is TDD: write the failing test first, see it fail, implement, see it pass, commit. The old modules (`pool.py`, `bus_monitor.py`, old `models.py` classes) stay in place through most phases and are deleted in Phase 8 — this keeps the test suite green while the new code is being built.

---

## File Structure

### New files (create)

- `jig/project.py` — `Project` model + `load_project`/`save_project` helpers for `.jig/project.json`.
- `jig/ticket.py` — `Ticket`, `TicketType`, `TicketStatus`, `Comment` models. Pure data, no persistence.
- `jig/store/tickets.py` — `TicketStore` class wrapping `TypedCollection[Ticket]` with indexes on `type`/`status`/`assignee`/`parent_id` and domain helpers (`create`, `update_status`, `find_in_progress_top_level`).
- `jig/store/comments.py` — `CommentStore` class wrapping `TypedCollection[Comment]` with indexes on `ticket_id`/`kind`/`author` and domain helpers (`post`, `phase_runs_for`, `commits_for`).
- `jig/skills/__init__.py` — empty marker so `importlib.resources` can find the skill files.
- `jig/skills/jig-mcp-tools.md` — universal skill. How to use ticket MCP tools.
- `jig/skills/git-conventions.md` — universal skill. Conventional commits + when to commit.
- `jig/skills/python.md` — applies_to `language: python`.
- `jig/skills/uv.md` — applies_to `language: python, package_manager: uv`.
- `jig/skills/pytest.md` — applies_to `language: python, test_command: <contains pytest>` (exact match on project fields only; we accept exact-match for MVP, the spec is explicit).
- `jig/skills/ruff.md` — applies_to `language: python`.
- `jig/skills/typescript.md` — applies_to `language: typescript`.
- `jig/skills/pnpm.md` — applies_to `language: typescript, package_manager: pnpm`.
- `jig/skills/vitest.md` — applies_to `language: typescript`.
- `jig/skill_loader.py` — `Skill` dataclass, `load_all_skills()`, `match_skills(project)` — reads frontmatter via `importlib.resources`.
- `jig/environment.py` — `load_environment_md(project_path)` returning the verbatim contents or empty string.
- `jig/prompt_builder.py` — `build_initial_prompt(role_cfg, spawn_reason, ticket, parent, comments, memories, project, skills, environment_md)` — assembles the layered prompt per the spec's injection order.
- `jig/ticket_mcp.py` — `handle_create_ticket`, `handle_read_ticket`, `handle_update_ticket`, `handle_comment_on_ticket`, `handle_list_tickets`, `handle_read_comments`, `handle_commit_progress`, `handle_record_learning`, `handle_request_context`. Replaces most of `mcp_tools.py`.
- `jig/runtime.py` — `SpawnReason` enum + `AgentSpawnContext` dataclass; owned here to avoid circular imports between `agent.py` and `orchestrator.py`.
- `tests/test_project.py`
- `tests/test_ticket_models.py`
- `tests/test_ticket_store.py`
- `tests/test_comment_store.py`
- `tests/test_skill_loader.py`
- `tests/test_environment.py`
- `tests/test_prompt_builder.py`
- `tests/test_ticket_mcp.py`
- `tests/test_orchestrator_dispatch.py`
- `tests/test_orchestrator_per_ticket.py`
- `tests/test_worktree_per_ticket.py`
- `tests/test_agent_streaming.py`

### Files to rewrite

- `jig/models.py` — delete `Issue`, `Task`, `IssueStatus`, `CompletionState`, `PhaseHistoryEntry`, `AgentMessage`, `MessageDirection`, `AgentStatus`, `AgentInstance`, `CompletionStatus`, `CompletionReport`. Update `AgentTypeConfig` (rename `system_prompt` → `phase_prompt`, add `response_prompt`, `can_message`). Keep `ProjectConfig`, `MergeStrategy`, `ProjectContext`, `PhaseConfig`, `WorkflowConfig` until Phase 8.
- `jig/persistence.py` — delete issue/task/agent-instance functions. Keep agent-type + workflow functions. Delete the old `load_skill(name)` signature (new skill loader lives in `jig/skill_loader.py`). Also delete the old `load_project(... ) -> ProjectConfig` and `save_project_context` / `load_project_context` helpers once the CLI has migrated to `jig.project.load_project` / `save_project` (Phase 9.1) — these names belong to the new `Project` module going forward.
- `jig/orchestrator.py` — rewrite from scratch. Keep the file, but replace contents with the new class shape.
- `jig/agent.py` — rewrite `run_agent` around streaming input mode and the new prompt builder. Keep `_sanitize_for_tui`, `_tool_detail` helpers.
- `jig/worktree.py` — `create_worktree` and `remove_worktree` take `ticket_id` only (no phase). Branch becomes `jig/{ticket_id}`. `merge_issue` renamed to `merge_ticket`.
- `jig/mcp_server.py` — rewire `create_agent_mcp_server` to register the new ticket tools from `ticket_mcp.py`. Delete `create_orchestrator_mcp_server` (the orchestrator never spoke MCP in practice).
- `jig/cli.py` — `init` creates `.jig/project.json` instead of `.jig/config.yaml` + `.jig/project_context.yaml`; drops `.jig/issues/` directory creation.
- `jig/ws_server.py` — add a bidirectional client → server command channel so the TUI can send `create_ticket`/`comment_on_ticket` back. This is a small additive change.

### Files to delete (Phase 8)

- `jig/pool.py`
- `jig/bus_monitor.py`
- `jig/mcp_tools.py` (replaced by `jig/ticket_mcp.py`)
- `tests/test_pool.py`
- `tests/test_bus_monitor.py`
- `tests/test_mcp_tools.py`
- `jig/defaults/skills/` (old skill directory, replaced by `jig/skills/`)

### Tests to rewrite

- `tests/test_orchestrator.py` — rewrite around ticket-based flow.
- `tests/test_models.py` — rewrite around ticket models.
- `tests/test_persistence.py` — cut issue/task/agent-instance coverage, keep agent-type + workflow.
- `tests/test_mcp_server.py` — rewrite around new tool surface.
- `tests/test_agent.py` — rewrite around streaming input.
- `tests/test_worktree.py` — rewrite around per-ticket worktrees.
- `tests/test_cli.py` — update for new `.jig/` layout.

---

# Phase 0 — Project model and ticket data model

### Task 0.1: Add `Project` model and persistence

**Files:**
- Create: `jig/project.py`
- Test: `tests/test_project.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_project.py
import json
from pathlib import Path

from jig.project import Project, load_project, save_project


def test_save_and_load_project(tmp_path: Path) -> None:
    project = Project(
        id="jig-itself",
        name="jig",
        path=str(tmp_path),
        default_branch="develop",
        description="multi-agent dev system",
        language="python",
        framework="",
        package_manager="uv",
        test_command="uv run pytest",
        build_command="",
    )
    save_project(tmp_path, project)

    assert (tmp_path / ".jig" / "project.json").is_file()
    loaded = load_project(tmp_path)
    assert loaded == project


def test_load_project_missing_raises(tmp_path: Path) -> None:
    import pytest
    with pytest.raises(FileNotFoundError):
        load_project(tmp_path)


def test_project_json_is_pretty(tmp_path: Path) -> None:
    project = Project(id="p1", name="p1", path=str(tmp_path))
    save_project(tmp_path, project)
    raw = (tmp_path / ".jig" / "project.json").read_text()
    data = json.loads(raw)
    assert data["id"] == "p1"
    assert "\n" in raw  # pretty-printed, not single-line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_project.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jig.project'`

- [ ] **Step 3: Write minimal implementation**

```python
# jig/project.py
import json
from pathlib import Path

from pydantic import BaseModel


class Project(BaseModel):
    id: str
    name: str
    path: str
    default_branch: str = "main"
    description: str = ""
    language: str = ""
    framework: str = ""
    package_manager: str = ""
    test_command: str = ""
    build_command: str = ""


def _project_file(project_path: Path) -> Path:
    return project_path / ".jig" / "project.json"


def save_project(project_path: Path, project: Project) -> None:
    path = _project_file(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(project.model_dump(), indent=2))


def load_project(project_path: Path) -> Project:
    path = _project_file(project_path)
    if not path.is_file():
        raise FileNotFoundError(f"Project file not found: {path}")
    return Project.model_validate_json(path.read_text())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_project.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/project.py tests/test_project.py
git commit -m "feat(project): add Project model with json persistence"
```

### Task 0.2: Add Ticket / TicketStatus / TicketType / Comment models

**Files:**
- Create: `jig/ticket.py`
- Test: `tests/test_ticket_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ticket_models.py
from datetime import datetime

import pytest
from pydantic import ValidationError

from jig.ticket import Comment, Ticket, TicketStatus, TicketType


def test_ticket_defaults() -> None:
    t = Ticket(
        type=TicketType.FEATURE,
        title="add search",
        created_by="user",
    )
    assert t.status == TicketStatus.OPEN
    assert t.description == ""
    assert t.assignee is None
    assert t.parent_id is None
    assert t.blocks == []
    assert t.blocked_by == []
    assert t.labels == []
    assert isinstance(t.created_at, datetime)
    assert isinstance(t.updated_at, datetime)


def test_ticket_all_statuses_accepted() -> None:
    for status in TicketStatus:
        t = Ticket(
            type=TicketType.TASK,
            title="t",
            created_by="orchestrator",
            status=status,
        )
        assert t.status == status


def test_ticket_all_types_accepted() -> None:
    for ttype in TicketType:
        t = Ticket(type=ttype, title="t", created_by="u")
        assert t.type == ttype


def test_comment_default_kind() -> None:
    c = Comment(ticket_id="T-1", author="dev", content="hello")
    assert c.kind == "comment"
    assert c.commit_sha is None
    assert c.phase_result is None
    assert c.phase_branch is None


def test_comment_phase_run() -> None:
    c = Comment(
        ticket_id="T-1",
        author="orchestrator",
        content="phase done",
        kind="phase_run",
        phase_result="success",
        phase_branch="jig/T-1",
    )
    assert c.kind == "phase_run"
    assert c.phase_result == "success"


def test_comment_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        Comment(ticket_id="T-1", author="dev", content="x", kind="gossip")


def test_comment_rejects_unknown_phase_result() -> None:
    with pytest.raises(ValidationError):
        Comment(
            ticket_id="T-1",
            author="o",
            content="x",
            kind="phase_run",
            phase_result="partial",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ticket_models.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jig.ticket'`

- [ ] **Step 3: Write minimal implementation**

```python
# jig/ticket.py
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import Field

from jig.store.models import StoreModel


class TicketType(str, Enum):
    FEATURE = "feature"
    BUG = "bug"
    CHORE = "chore"
    TASK = "task"
    QUESTION = "question"


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    NEEDS_INFO = "needs_info"
    FAILED = "failed"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Ticket(StoreModel):
    type: TicketType
    status: TicketStatus = TicketStatus.OPEN
    title: str
    description: str = ""
    assignee: str | None = None
    parent_id: str | None = None
    blocks: list[str] = []
    blocked_by: list[str] = []
    labels: list[str] = []
    created_by: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Comment(StoreModel):
    ticket_id: str
    author: str
    content: str
    kind: Literal[
        "comment",
        "commit",
        "phase_run",
        "decision",
        "status_change",
    ] = "comment"
    commit_sha: str | None = None
    phase_result: Literal["success", "failed", "blocked", "needs_info"] | None = None
    phase_branch: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ticket_models.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/ticket.py tests/test_ticket_models.py
git commit -m "feat(ticket): add Ticket and Comment models"
```

### Task 0.3: Update `AgentTypeConfig`

**Files:**
- Modify: `jig/models.py`
- Test: `tests/test_models.py` (add new tests alongside existing)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py — add these tests
from jig.models import AgentTypeConfig


def test_agent_type_new_fields() -> None:
    cfg = AgentTypeConfig(
        role="dev",
        phase_prompt="You are a developer.",
        response_prompt="You are answering a question.",
        allowed_tools=["Read", "Edit"],
        can_message=["spec-writer", "user"],
    )
    assert cfg.phase_prompt == "You are a developer."
    assert cfg.response_prompt == "You are answering a question."
    assert cfg.can_message == ["spec-writer", "user"]


def test_agent_type_response_prompt_optional() -> None:
    cfg = AgentTypeConfig(role="dev", phase_prompt="be a dev")
    assert cfg.response_prompt == ""
    assert cfg.can_message == []
    assert cfg.default_context == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py::test_agent_type_new_fields tests/test_models.py::test_agent_type_response_prompt_optional -v`
Expected: FAIL, missing `phase_prompt` field (currently named `system_prompt`).

- [ ] **Step 3: Update `AgentTypeConfig` in `jig/models.py`**

Replace the existing class with:

```python
class AgentTypeConfig(BaseModel):
    role: str
    phase_prompt: str
    response_prompt: str = ""
    allowed_tools: list[str] = []
    can_message: list[str] = []
    default_context: list[str] = []
```

- [ ] **Step 4: Update existing call sites to compile**

Every `cfg.system_prompt` reference in the codebase must become `cfg.phase_prompt`. Grep for them:

Run: `uv run grep -rn "system_prompt" jig/ tests/`
Update each match that refers to `AgentTypeConfig.system_prompt`. The only call site inside `jig/` as of this plan is `jig/agent.py:183` where `system_prompt=agent_type.system_prompt` is passed to `ClaudeAgentOptions`. Update to `system_prompt=agent_type.phase_prompt`. This is a temporary bridge — Phase 4 will rewrite `run_agent` entirely.

Also update any YAML fixtures under `jig/defaults/agent_types/` that set `system_prompt:` to `phase_prompt:`.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest -x`
Expected: existing tests that referenced `system_prompt` now work with `phase_prompt`; new tests pass; total test count increases by 2.

- [ ] **Step 6: Commit**

```bash
git add jig/models.py jig/agent.py jig/defaults/agent_types/ tests/test_models.py
git commit -m "feat(models): rename system_prompt to phase_prompt, add response_prompt and can_message"
```

---

# Phase 1 — Persistence layer (ticket and comment stores)

### Task 1.1: `TicketStore` with indexes

**Files:**
- Create: `jig/store/tickets.py`
- Test: `tests/test_ticket_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ticket_store.py
from pathlib import Path

import pytest

from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, TicketType


@pytest.mark.asyncio
async def test_create_and_get(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    t = Ticket(type=TicketType.FEATURE, title="f1", created_by="user")
    tid = await store.create(t)
    loaded = await store.get(tid)
    assert loaded is not None
    assert loaded.title == "f1"
    assert loaded.id == tid


@pytest.mark.asyncio
async def test_find_in_progress_top_level(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    f = Ticket(type=TicketType.FEATURE, title="f", created_by="u", status=TicketStatus.IN_PROGRESS)
    b = Ticket(type=TicketType.BUG, title="b", created_by="u", status=TicketStatus.IN_PROGRESS)
    c = Ticket(type=TicketType.CHORE, title="c", created_by="u", status=TicketStatus.OPEN)
    task = Ticket(type=TicketType.TASK, title="t", created_by="o", status=TicketStatus.IN_PROGRESS)
    await store.create(f)
    await store.create(b)
    await store.create(c)
    await store.create(task)

    found = await store.find_in_progress_top_level()
    titles = sorted(t.title for t in found)
    assert titles == ["b", "f"]


@pytest.mark.asyncio
async def test_find_by_assignee_uses_index(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    await store.create(Ticket(type=TicketType.TASK, title="a", created_by="o", assignee="dev"))
    await store.create(Ticket(type=TicketType.TASK, title="b", created_by="o", assignee="qa"))
    await store.create(Ticket(type=TicketType.TASK, title="c", created_by="o", assignee="dev"))

    dev_tickets = await store.find_by_assignee("dev")
    assert sorted(t.title for t in dev_tickets) == ["a", "c"]


@pytest.mark.asyncio
async def test_update_status_bumps_updated_at(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(Ticket(type=TicketType.FEATURE, title="f", created_by="u"))
    before = (await store.get(tid)).updated_at
    await store.update_status(tid, TicketStatus.IN_PROGRESS)
    after = await store.get(tid)
    assert after.status == TicketStatus.IN_PROGRESS
    assert after.updated_at >= before


@pytest.mark.asyncio
async def test_reload_replays_log(tmp_path: Path) -> None:
    path = tmp_path / "tickets.jsonl"
    store = TicketStore(path)
    await store.load()
    tid = await store.create(Ticket(type=TicketType.BUG, title="b", created_by="u"))
    await store.update_status(tid, TicketStatus.RESOLVED)

    store2 = TicketStore(path)
    await store2.load()
    loaded = await store2.get(tid)
    assert loaded.status == TicketStatus.RESOLVED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ticket_store.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jig.store.tickets'`

- [ ] **Step 3: Write minimal implementation**

```python
# jig/store/tickets.py
from datetime import datetime, timezone
from pathlib import Path

from jig.store.models import TypedCollection
from jig.ticket import Ticket, TicketStatus, TicketType

TOP_LEVEL_TYPES = {TicketType.FEATURE, TicketType.BUG, TicketType.CHORE}


class TicketStore:
    def __init__(self, path: Path) -> None:
        self._collection: TypedCollection[Ticket] = TypedCollection(
            path,
            model=Ticket,
            index_fields=["type", "status", "assignee", "parent_id"],
        )

    async def load(self) -> None:
        await self._collection.load()

    async def create(self, ticket: Ticket) -> str:
        return await self._collection.insert(ticket)

    async def get(self, ticket_id: str) -> Ticket | None:
        return await self._collection.get(ticket_id)

    async def update(self, ticket_id: str, **fields) -> Ticket:
        fields["updated_at"] = datetime.now(timezone.utc)
        await self._collection.update(ticket_id, fields)
        loaded = await self._collection.get(ticket_id)
        assert loaded is not None
        return loaded

    async def update_status(self, ticket_id: str, status: TicketStatus) -> Ticket:
        return await self.update(ticket_id, status=status)

    async def find_in_progress_top_level(self) -> list[Ticket]:
        results: list[Ticket] = []
        for ttype in TOP_LEVEL_TYPES:
            found = await self._collection.find_where(
                type=ttype, status=TicketStatus.IN_PROGRESS
            )
            results.extend(found)
        return results

    async def find_by_assignee(self, assignee: str) -> list[Ticket]:
        return await self._collection.find_where(assignee=assignee)

    async def find_by_parent(self, parent_id: str) -> list[Ticket]:
        return await self._collection.find_where(parent_id=parent_id)

    async def list_all(self) -> list[Ticket]:
        return await self._collection.find()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ticket_store.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/store/tickets.py tests/test_ticket_store.py
git commit -m "feat(store): add TicketStore with type/status/assignee/parent_id indexes"
```

### Task 1.2: `CommentStore` with indexes

**Files:**
- Create: `jig/store/comments.py`
- Test: `tests/test_comment_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_comment_store.py
from pathlib import Path

import pytest

from jig.store.comments import CommentStore
from jig.ticket import Comment


@pytest.mark.asyncio
async def test_post_and_read(tmp_path: Path) -> None:
    store = CommentStore(tmp_path / "comments.jsonl")
    await store.load()
    cid = await store.post(Comment(ticket_id="T-1", author="dev", content="hi"))
    loaded = (await store.for_ticket("T-1"))[0]
    assert loaded.id == cid
    assert loaded.content == "hi"


@pytest.mark.asyncio
async def test_phase_runs_for_ticket(tmp_path: Path) -> None:
    store = CommentStore(tmp_path / "comments.jsonl")
    await store.load()
    await store.post(Comment(ticket_id="T-1", author="o", content="a", kind="comment"))
    await store.post(Comment(
        ticket_id="T-1", author="o", content="p1",
        kind="phase_run", phase_result="failed", phase_branch="jig/T-1",
    ))
    await store.post(Comment(
        ticket_id="T-1", author="o", content="p2",
        kind="phase_run", phase_result="success", phase_branch="jig/T-1",
    ))

    runs = await store.phase_runs_for("T-1")
    assert [r.phase_result for r in runs] == ["failed", "success"]


@pytest.mark.asyncio
async def test_commits_for_ticket(tmp_path: Path) -> None:
    store = CommentStore(tmp_path / "comments.jsonl")
    await store.load()
    await store.post(Comment(ticket_id="T-1", author="dev", content="x"))
    await store.post(Comment(
        ticket_id="T-1", author="dev", content="step 1",
        kind="commit", commit_sha="abc123",
    ))
    commits = await store.commits_for("T-1")
    assert [c.commit_sha for c in commits] == ["abc123"]


@pytest.mark.asyncio
async def test_for_ticket_is_chronological(tmp_path: Path) -> None:
    store = CommentStore(tmp_path / "comments.jsonl")
    await store.load()
    first = await store.post(Comment(ticket_id="T-1", author="a", content="1"))
    second = await store.post(Comment(ticket_id="T-1", author="a", content="2"))
    third = await store.post(Comment(ticket_id="T-1", author="a", content="3"))
    ids = [c.id for c in await store.for_ticket("T-1")]
    assert ids == [first, second, third]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_comment_store.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jig.store.comments'`

- [ ] **Step 3: Write minimal implementation**

```python
# jig/store/comments.py
from pathlib import Path

from jig.store.models import TypedCollection
from jig.ticket import Comment


class CommentStore:
    def __init__(self, path: Path) -> None:
        self._collection: TypedCollection[Comment] = TypedCollection(
            path,
            model=Comment,
            index_fields=["ticket_id", "kind", "author"],
        )

    async def load(self) -> None:
        await self._collection.load()

    async def post(self, comment: Comment) -> str:
        return await self._collection.insert(comment)

    async def for_ticket(self, ticket_id: str) -> list[Comment]:
        found = await self._collection.find_where(ticket_id=ticket_id)
        found.sort(key=lambda c: c.created_at)
        return found

    async def phase_runs_for(self, ticket_id: str) -> list[Comment]:
        all_for = await self.for_ticket(ticket_id)
        return [c for c in all_for if c.kind == "phase_run"]

    async def commits_for(self, ticket_id: str) -> list[Comment]:
        all_for = await self.for_ticket(ticket_id)
        return [c for c in all_for if c.kind == "commit"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_comment_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/store/comments.py tests/test_comment_store.py
git commit -m "feat(store): add CommentStore with ticket_id/kind/author indexes"
```

### Task 1.3: Add role-keyed learnings to `MemoryStore`

**Why:** The existing `MemoryStore.add_learning(issue_id, phase, content)` keys learnings on issue/phase. The new design needs **role-keyed** learnings (persistent across tickets, per-project) so `record_learning` and the prompt builder can query memories by the agent's role. We add new methods without breaking the existing ones — the old issue-keyed methods can be removed in a later cleanup after all call sites migrate.

**Files:**
- Modify: `jig/store/memory.py`
- Modify: `tests/test_memory_store.py` (or create if missing)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_store.py — add these
from pathlib import Path

import pytest

from jig.store.memory import MemoryStore


@pytest.mark.asyncio
async def test_add_and_get_role_learning(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memories.jsonl")
    await store.load()
    await store.add_role_learning(role="dev", content="always uv run pytest")
    await store.add_role_learning(role="dev", content="use pathlib not os.path")
    await store.add_role_learning(role="qa", content="run full suite before signoff")

    dev_memories = await store.get_role_learnings("dev")
    assert [m.content for m in dev_memories] == [
        "always uv run pytest",
        "use pathlib not os.path",
    ]
    qa_memories = await store.get_role_learnings("qa")
    assert [m.content for m in qa_memories] == ["run full suite before signoff"]


@pytest.mark.asyncio
async def test_role_learnings_empty_when_none(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memories.jsonl")
    await store.load()
    assert await store.get_role_learnings("dev") == []


@pytest.mark.asyncio
async def test_role_learnings_persist_across_reload(tmp_path: Path) -> None:
    path = tmp_path / "memories.jsonl"
    store = MemoryStore(path)
    await store.load()
    await store.add_role_learning(role="dev", content="x")

    store2 = MemoryStore(path)
    await store2.load()
    assert [m.content for m in await store2.get_role_learnings("dev")] == ["x"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_memory_store.py::test_add_and_get_role_learning -v`
Expected: FAIL, `AttributeError: 'MemoryStore' object has no attribute 'add_role_learning'`

- [ ] **Step 3: Implement the role-keyed methods**

Open `jig/store/memory.py` and look at the existing `Learning` model. Add a new `RoleLearning` model with `role` + `content` + `timestamp`, and a second `TypedCollection` for role learnings, or — simpler — add a `role` field to `Learning` and route through the existing collection.

Simplest path: add a new field `role: str = ""` to `Learning`, index it, and add new methods alongside the existing ones:

```python
# jig/store/memory.py — append to class MemoryStore

    async def add_role_learning(self, *, role: str, content: str) -> str:
        learning = Learning(
            issue_id="",  # unused for role-keyed learnings
            phase="",
            content=content,
            role=role,
        )
        return await self._learnings.insert(learning)

    async def get_role_learnings(
        self, role: str, limit: int = 20
    ) -> list[Learning]:
        results = await self._learnings.find_where(role=role)
        results.sort(key=lambda l: l.timestamp)
        return results[:limit]
```

And update the `Learning` Pydantic model to include `role: str = ""`. Update the `TypedCollection` index_fields list to include `"role"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_memory_store.py -v`
Expected: PASS (3 new tests)

- [ ] **Step 5: Commit**

```bash
git add jig/store/memory.py tests/test_memory_store.py
git commit -m "feat(memory): add role-keyed learnings alongside issue-keyed"
```

### Task 1.4: Wire ticket + comment stores into the package exports

**Files:**
- Modify: `jig/store/__init__.py`

- [ ] **Step 1: Export the stores**

Add to `jig/store/__init__.py`:

```python
from jig.store.tickets import TicketStore
from jig.store.comments import CommentStore
```

And include them in `__all__` if that list exists.

- [ ] **Step 2: Smoke-test the import**

Run: `uv run python -c "from jig.store import TicketStore, CommentStore; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Run the full store test suite**

Run: `uv run pytest tests/test_ticket_store.py tests/test_comment_store.py tests/test_store_core.py -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add jig/store/__init__.py
git commit -m "chore(store): export TicketStore and CommentStore"
```

---

# Phase 2 — MCP tool surface (ticket operations + commit_progress)

### Task 2.1: `create_ticket` handler

**Files:**
- Create: `jig/ticket_mcp.py`
- Test: `tests/test_ticket_mcp.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ticket_mcp.py
from pathlib import Path

import pytest

from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.tickets import TicketStore
from jig.ticket import TicketStatus, TicketType
from jig.ticket_mcp import handle_create_ticket


@pytest.fixture
async def stores(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    comments = CommentStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await comments.load()
    await bus.load()
    return tickets, comments, bus


@pytest.mark.asyncio
async def test_create_ticket_persists(stores) -> None:
    tickets, comments, bus = stores
    ticket_id = await handle_create_ticket(
        tickets=tickets,
        comments=comments,
        bus=bus,
        sender="user",
        args={
            "type": "feature",
            "title": "Add search",
            "description": "users want to search",
        },
    )
    loaded = await tickets.get(ticket_id)
    assert loaded is not None
    assert loaded.title == "Add search"
    assert loaded.type == TicketType.FEATURE
    assert loaded.created_by == "user"
    assert loaded.status == TicketStatus.OPEN


@pytest.mark.asyncio
async def test_create_ticket_publishes_bus_event(stores) -> None:
    tickets, comments, bus = stores
    queue = await bus.subscribe("orchestrator")
    await handle_create_ticket(
        tickets=tickets,
        comments=comments,
        bus=bus,
        sender="user",
        args={"type": "bug", "title": "crash"},
    )
    msg = await queue.get()
    assert msg.topic == "orchestrator"
    assert msg.payload["kind"] == "ticket_created"
    assert msg.payload["type"] == "bug"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ticket_mcp.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jig.ticket_mcp'`

- [ ] **Step 3: Write minimal implementation**

```python
# jig/ticket_mcp.py
from jig.store import Message, MessageBus, MessageType
from jig.store.comments import CommentStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketType


async def handle_create_ticket(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    bus: MessageBus,
    sender: str,
    args: dict,
) -> str:
    ticket = Ticket(
        type=TicketType(args["type"]),
        title=args["title"],
        description=args.get("description", ""),
        assignee=args.get("assignee"),
        parent_id=args.get("parent_id"),
        labels=args.get("labels", []),
        created_by=sender,
    )
    ticket_id = await tickets.create(ticket)
    await bus.publish(Message(
        sender=sender,
        to=ticket.assignee or "orchestrator",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "ticket_created",
            "ticket_id": ticket_id,
            "type": ticket.type.value,
            "assignee": ticket.assignee,
            "parent_id": ticket.parent_id,
        },
        topic="orchestrator",
    ))
    # Also publish to the ticket-specific topic so subscribers see it:
    await bus.publish(Message(
        sender=sender,
        to=ticket.assignee or "broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload={"kind": "ticket_created", "ticket_id": ticket_id},
        topic=f"tickets.{ticket_id}",
    ))
    return ticket_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ticket_mcp.py::test_create_ticket_persists tests/test_ticket_mcp.py::test_create_ticket_publishes_bus_event -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/ticket_mcp.py tests/test_ticket_mcp.py
git commit -m "feat(mcp): add handle_create_ticket"
```

### Task 2.2: `read_ticket`, `list_tickets`, `read_comments`

**Files:**
- Modify: `jig/ticket_mcp.py`
- Modify: `tests/test_ticket_mcp.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ticket_mcp.py`:

```python
from jig.ticket_mcp import (
    handle_create_ticket,
    handle_read_ticket,
    handle_list_tickets,
    handle_read_comments,
    handle_comment_on_ticket,
)


@pytest.mark.asyncio
async def test_read_ticket(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="user",
        args={"type": "feature", "title": "f"},
    )
    loaded = await handle_read_ticket(tickets=tickets, ticket_id=tid)
    assert loaded.title == "f"


@pytest.mark.asyncio
async def test_read_ticket_missing_raises(stores) -> None:
    tickets, _, _ = stores
    with pytest.raises(KeyError):
        await handle_read_ticket(tickets=tickets, ticket_id="nope")


@pytest.mark.asyncio
async def test_list_tickets_filtered(stores) -> None:
    tickets, comments, bus = stores
    await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f1"},
    )
    await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "bug", "title": "b1"},
    )
    features = await handle_list_tickets(tickets=tickets, args={"type": "feature"})
    assert [t.title for t in features] == ["f1"]


@pytest.mark.asyncio
async def test_read_comments_filtered(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "task", "title": "t"},
    )
    await handle_comment_on_ticket(
        tickets=tickets, comments=comments, bus=bus,
        sender="dev", args={"ticket_id": tid, "content": "hello"},
    )
    all_c = await handle_read_comments(comments=comments, ticket_id=tid)
    assert [c.content for c in all_c] == ["hello"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticket_mcp.py -v`
Expected: FAIL, missing handlers and missing `handle_comment_on_ticket`.

- [ ] **Step 3: Implement the handlers**

Add to `jig/ticket_mcp.py`:

```python
from jig.ticket import TicketStatus


async def handle_read_ticket(*, tickets: TicketStore, ticket_id: str) -> Ticket:
    loaded = await tickets.get(ticket_id)
    if loaded is None:
        raise KeyError(f"ticket {ticket_id} not found")
    return loaded


async def handle_list_tickets(
    *, tickets: TicketStore, args: dict
) -> list[Ticket]:
    ttype = TicketType(args["type"]) if "type" in args else None
    status = TicketStatus(args["status"]) if "status" in args else None
    assignee = args.get("assignee")
    parent_id = args.get("parent_id")

    if assignee is not None:
        pool = await tickets.find_by_assignee(assignee)
    elif parent_id is not None:
        pool = await tickets.find_by_parent(parent_id)
    else:
        pool = await tickets.list_all()

    def keep(t: Ticket) -> bool:
        if ttype is not None and t.type != ttype:
            return False
        if status is not None and t.status != status:
            return False
        return True

    return [t for t in pool if keep(t)]


async def handle_read_comments(
    *, comments: CommentStore, ticket_id: str, kind: str | None = None
) -> list:
    all_for = await comments.for_ticket(ticket_id)
    if kind is None:
        return all_for
    return [c for c in all_for if c.kind == kind]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticket_mcp.py::test_read_ticket tests/test_ticket_mcp.py::test_list_tickets_filtered -v`
Expected: PASS

`test_read_comments_filtered` still fails because `handle_comment_on_ticket` isn't implemented yet — that comes in Task 2.3. Mark it as `xfail` temporarily or skip; simpler: leave it failing and implement 2.3 immediately.

- [ ] **Step 5: Commit**

```bash
git add jig/ticket_mcp.py tests/test_ticket_mcp.py
git commit -m "feat(mcp): add read_ticket, list_tickets, read_comments handlers"
```

### Task 2.3: `comment_on_ticket` + `update_ticket` (with allowlist + auto status_change)

**Files:**
- Modify: `jig/ticket_mcp.py`
- Modify: `tests/test_ticket_mcp.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_ticket_mcp.py`:

```python
from jig.models import AgentTypeConfig
from jig.ticket_mcp import handle_update_ticket


@pytest.mark.asyncio
async def test_comment_on_ticket_allowlist_enforced(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "task", "title": "t", "assignee": "spec-writer"},
    )
    dev_cfg = AgentTypeConfig(
        role="dev", phase_prompt="", can_message=["user"],  # NOT spec-writer
    )
    with pytest.raises(PermissionError):
        await handle_comment_on_ticket(
            tickets=tickets, comments=comments, bus=bus,
            sender="dev", sender_cfg=dev_cfg,
            args={"ticket_id": tid, "content": "hi"},
        )


@pytest.mark.asyncio
async def test_comment_on_ticket_rejects_system_kinds(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "task", "title": "t"},
    )
    with pytest.raises(ValueError):
        await handle_comment_on_ticket(
            tickets=tickets, comments=comments, bus=bus,
            sender="dev", sender_cfg=None,
            args={"ticket_id": tid, "content": "x", "kind": "phase_run"},
        )


@pytest.mark.asyncio
async def test_update_ticket_status_emits_status_change_comment(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f"},
    )
    await handle_update_ticket(
        tickets=tickets, comments=comments, bus=bus,
        sender="orchestrator",
        args={"ticket_id": tid, "status": "in_progress"},
    )
    status_changes = [
        c for c in await comments.for_ticket(tid) if c.kind == "status_change"
    ]
    assert len(status_changes) == 1
    assert "in_progress" in status_changes[0].content


@pytest.mark.asyncio
async def test_update_ticket_non_status_field(stores) -> None:
    tickets, comments, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f"},
    )
    await handle_update_ticket(
        tickets=tickets, comments=comments, bus=bus,
        sender="orchestrator",
        args={"ticket_id": tid, "description": "more detail"},
    )
    loaded = await tickets.get(tid)
    assert loaded.description == "more detail"
    # no status_change comment on non-status updates
    assert not any(c.kind == "status_change" for c in await comments.for_ticket(tid))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticket_mcp.py -v`
Expected: new tests fail with missing handlers.

- [ ] **Step 3: Implement the handlers**

Add to `jig/ticket_mcp.py`:

```python
from jig.models import AgentTypeConfig
from jig.ticket import Comment


_WRITABLE_KINDS = {"comment", "decision"}


async def handle_comment_on_ticket(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    bus: MessageBus,
    sender: str,
    sender_cfg: AgentTypeConfig | None,
    args: dict,
) -> str:
    kind = args.get("kind", "comment")
    if kind not in _WRITABLE_KINDS:
        raise ValueError(
            f"kind {kind!r} is reserved for system primitives; "
            f"agents may only write {sorted(_WRITABLE_KINDS)}"
        )

    ticket_id = args["ticket_id"]
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        raise KeyError(f"ticket {ticket_id} not found")

    # Allowlist check — only applies when sender_cfg is provided (agents).
    # The orchestrator and user pass sender_cfg=None to bypass.
    if sender_cfg is not None and ticket.assignee:
        target = ticket.assignee
        allowed = set(sender_cfg.can_message) | {"orchestrator"}
        if target not in allowed:
            raise PermissionError(
                f"role {sender_cfg.role!r} is not allowed to message "
                f"role {target!r} (can_message={sender_cfg.can_message})"
            )

    comment = Comment(
        ticket_id=ticket_id,
        author=sender,
        content=args["content"],
        kind=kind,
    )
    cid = await comments.post(comment)

    await bus.publish(Message(
        sender=sender,
        to=ticket.assignee or "broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "comment_posted",
            "ticket_id": ticket_id,
            "comment_id": cid,
            "author": sender,
            "content": args["content"],
        },
        topic=f"tickets.{ticket_id}",
    ))
    return cid


async def handle_update_ticket(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    bus: MessageBus,
    sender: str,
    args: dict,
) -> Ticket:
    ticket_id = args.pop("ticket_id")
    before = await tickets.get(ticket_id)
    if before is None:
        raise KeyError(f"ticket {ticket_id} not found")

    update_fields: dict = {}
    for key, value in args.items():
        if key == "status":
            update_fields["status"] = TicketStatus(value)
        else:
            update_fields[key] = value

    updated = await tickets.update(ticket_id, **update_fields)

    # Auto-emit status_change comment on status transitions
    if "status" in update_fields and update_fields["status"] != before.status:
        await comments.post(Comment(
            ticket_id=ticket_id,
            author=sender,
            content=f"status {before.status.value} -> {updated.status.value}",
            kind="status_change",
        ))

    await bus.publish(Message(
        sender=sender,
        to=updated.assignee or "broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "ticket_updated",
            "ticket_id": ticket_id,
            "status": updated.status.value,
        },
        topic=f"tickets.{ticket_id}",
    ))
    return updated
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticket_mcp.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add jig/ticket_mcp.py tests/test_ticket_mcp.py
git commit -m "feat(mcp): add comment_on_ticket and update_ticket handlers with allowlist + status_change"
```

### Task 2.4: `commit_progress` (git + comment + bus)

**Files:**
- Modify: `jig/ticket_mcp.py`
- Modify: `tests/test_ticket_mcp.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ticket_mcp.py
@pytest.mark.asyncio
async def test_commit_progress_creates_commit_and_comment(stores, tmp_path) -> None:
    import subprocess
    tickets, comments, bus = stores

    # set up a real git worktree
    work = tmp_path / "worktree"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    (work / "a.txt").write_text("hello")

    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f"},
    )
    from jig.ticket_mcp import handle_commit_progress
    result = await handle_commit_progress(
        tickets=tickets, comments=comments, bus=bus,
        sender="dev",
        worktree_path=work,
        args={"ticket_id": tid, "message": "add a.txt"},
    )
    assert "sha" in result
    assert result["sha"]  # non-empty
    commit_comments = await comments.commits_for(tid)
    assert len(commit_comments) == 1
    assert commit_comments[0].commit_sha == result["sha"]
    assert commit_comments[0].content == "add a.txt"


@pytest.mark.asyncio
async def test_commit_progress_nothing_to_commit_returns_none_sha(stores, tmp_path) -> None:
    import subprocess
    tickets, comments, bus = stores
    work = tmp_path / "worktree2"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=work, check=True)

    tid = await handle_create_ticket(
        tickets=tickets, comments=comments, bus=bus, sender="u",
        args={"type": "feature", "title": "f"},
    )
    from jig.ticket_mcp import handle_commit_progress
    result = await handle_commit_progress(
        tickets=tickets, comments=comments, bus=bus,
        sender="dev", worktree_path=work,
        args={"ticket_id": tid, "message": "noop"},
    )
    # Nothing staged: no sha, no commit comment recorded
    assert result["sha"] is None
    assert await comments.commits_for(tid) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticket_mcp.py::test_commit_progress_creates_commit_and_comment -v`
Expected: FAIL, missing `handle_commit_progress`.

- [ ] **Step 3: Implement the handler**

Add to `jig/ticket_mcp.py`:

```python
from pathlib import Path

from jig.worktree import commit_worktree


async def handle_commit_progress(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    bus: MessageBus,
    sender: str,
    worktree_path: Path,
    args: dict,
) -> dict:
    ticket_id = args["ticket_id"]
    message = args["message"]
    if await tickets.get(ticket_id) is None:
        raise KeyError(f"ticket {ticket_id} not found")

    sha = await commit_worktree(worktree_path, message)
    if sha is None:
        # nothing to commit — still return a result but don't file a comment
        return {"sha": None, "comment_id": None}

    cid = await comments.post(Comment(
        ticket_id=ticket_id,
        author=sender,
        content=message,
        kind="commit",
        commit_sha=sha,
    ))

    await bus.publish(Message(
        sender=sender,
        to="broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "commit_recorded",
            "ticket_id": ticket_id,
            "sha": sha,
            "message": message,
        },
        topic=f"tickets.{ticket_id}",
    ))
    return {"sha": sha, "comment_id": cid}
```

Note: this uses the *current* `commit_worktree` in `jig/worktree.py`, which already commits in the given worktree. Phase 6 will rewrite the rest of `worktree.py`, but `commit_worktree` stays.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticket_mcp.py::test_commit_progress_creates_commit_and_comment tests/test_ticket_mcp.py::test_commit_progress_nothing_to_commit_returns_none_sha -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/ticket_mcp.py tests/test_ticket_mcp.py
git commit -m "feat(mcp): add commit_progress (git+comment+bus)"
```

### Task 2.5: `record_learning` + migrated `request_context`

**Files:**
- Modify: `jig/ticket_mcp.py`
- Modify: `tests/test_ticket_mcp.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_record_learning_writes_to_memory_store(tmp_path) -> None:
    from jig.store.memory import MemoryStore
    from jig.ticket_mcp import handle_record_learning
    memory = MemoryStore(tmp_path / "memories.jsonl")
    await memory.load()
    await handle_record_learning(
        memory=memory, role="dev",
        args={"content": "always use uv run"},
    )
    learnings = await memory.get_role_learnings("dev")
    assert [l.content for l in learnings] == ["always use uv run"]


@pytest.mark.asyncio
async def test_request_context_reads_worktree_file(tmp_path) -> None:
    from jig.ticket_mcp import handle_request_context
    work = tmp_path / "w"
    work.mkdir()
    (work / "README.md").write_text("hello")
    result = await handle_request_context(
        worktree_path=work, args={"path": "README.md"},
    )
    assert result == "hello"


@pytest.mark.asyncio
async def test_request_context_missing_file(tmp_path) -> None:
    from jig.ticket_mcp import handle_request_context
    work = tmp_path / "w"
    work.mkdir()
    result = await handle_request_context(
        worktree_path=work, args={"path": "nope.txt"},
    )
    assert "not found" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ticket_mcp.py -v -k "record_learning or request_context"`
Expected: FAIL, missing handlers.

- [ ] **Step 3: Implement the handlers**

Add to `jig/ticket_mcp.py`:

```python
from jig.store.memory import MemoryStore


async def handle_record_learning(
    *, memory: MemoryStore, role: str, args: dict
) -> str:
    await memory.add_role_learning(role=role, content=args["content"])
    return f"learning recorded for {role}"


async def handle_request_context(
    *, worktree_path: Path, args: dict
) -> str:
    target = worktree_path / args["path"]
    if not target.is_file():
        return f"File not found: {args['path']}"
    try:
        return target.read_text()
    except Exception as exc:  # pragma: no cover
        return f"Error reading {args['path']}: {exc}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ticket_mcp.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add jig/ticket_mcp.py tests/test_ticket_mcp.py
git commit -m "feat(mcp): add record_learning and migrate request_context"
```

### Task 2.6: Rewire `create_agent_mcp_server` to the new tool surface

**Files:**
- Modify: `jig/mcp_server.py`
- Test: `tests/test_mcp_server.py` (rewrite agent-facing tests)

- [ ] **Step 1: Write the failing test**

Replace the body of `tests/test_mcp_server.py` with tests that construct the new server and assert the registered tool names:

```python
# tests/test_mcp_server.py
from pathlib import Path

import pytest

from jig.mcp_server import create_agent_mcp_server
from jig.models import AgentTypeConfig
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore


@pytest.mark.asyncio
async def test_agent_mcp_server_registers_expected_tools(tmp_path: Path) -> None:
    tickets = TicketStore(tmp_path / "tickets.jsonl"); await tickets.load()
    comments = CommentStore(tmp_path / "comments.jsonl"); await comments.load()
    memory = MemoryStore(tmp_path / "memories.jsonl"); await memory.load()
    bus = MessageBus(tmp_path / "messages.jsonl"); await bus.load()
    cfg = AgentTypeConfig(role="dev", phase_prompt="", can_message=["user"])

    server = create_agent_mcp_server(
        tickets=tickets,
        comments=comments,
        memory=memory,
        bus=bus,
        agent_role="dev",
        agent_cfg=cfg,
        worktree_path=tmp_path / "worktree",
    )
    # The claude_agent_sdk exposes .tool_names or equivalent; inspect the server's
    # registered tools. See existing usage of create_sdk_mcp_server in
    # jig/mcp_server.py for whichever accessor is idiomatic.
    tool_names = {t.name for t in server.tools}
    assert tool_names == {
        "create_ticket",
        "read_ticket",
        "update_ticket",
        "comment_on_ticket",
        "list_tickets",
        "read_comments",
        "commit_progress",
        "record_learning",
        "request_context",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: FAIL, signature mismatch on `create_agent_mcp_server`.

- [ ] **Step 3: Rewrite `create_agent_mcp_server`**

Open `jig/mcp_server.py`. Replace the existing `create_agent_mcp_server` with:

```python
def create_agent_mcp_server(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    memory: MemoryStore,
    bus: MessageBus,
    agent_role: str,
    agent_cfg: AgentTypeConfig,
    worktree_path: Path,
):
    """MCP server exposing the ticket tool surface to an agent."""
    from claude_agent_sdk import create_sdk_mcp_server, tool
    from jig import ticket_mcp

    @tool(
        name="create_ticket",
        description="Create a new ticket (feature, bug, chore, task, or question)",
        input_schema={
            "type": {"type": "string", "enum": ["feature", "bug", "chore", "task", "question"]},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "assignee": {"type": "string"},
            "parent_id": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
        },
    )
    async def create_ticket(args: dict) -> dict:
        tid = await ticket_mcp.handle_create_ticket(
            tickets=tickets, comments=comments, bus=bus,
            sender=agent_role, args=args,
        )
        return {"content": [{"type": "text", "text": tid}]}

    @tool(
        name="read_ticket",
        description="Read a single ticket by id",
        input_schema={"ticket_id": {"type": "string"}},
    )
    async def read_ticket(args: dict) -> dict:
        t = await ticket_mcp.handle_read_ticket(
            tickets=tickets, ticket_id=args["ticket_id"],
        )
        return {"content": [{"type": "text", "text": t.model_dump_json()}]}

    @tool(
        name="update_ticket",
        description="Update fields on a ticket (status, description, assignee, labels, ...)",
        input_schema={
            "ticket_id": {"type": "string"},
            "status": {"type": "string"},
            "description": {"type": "string"},
            "assignee": {"type": "string"},
            "labels": {"type": "array", "items": {"type": "string"}},
        },
    )
    async def update_ticket(args: dict) -> dict:
        updated = await ticket_mcp.handle_update_ticket(
            tickets=tickets, comments=comments, bus=bus,
            sender=agent_role, args=args,
        )
        return {"content": [{"type": "text", "text": updated.model_dump_json()}]}

    @tool(
        name="comment_on_ticket",
        description="Post a free-form comment (kind='comment') or a design rationale (kind='decision') on a ticket",
        input_schema={
            "ticket_id": {"type": "string"},
            "content": {"type": "string"},
            "kind": {"type": "string", "enum": ["comment", "decision"]},
        },
    )
    async def comment_on_ticket(args: dict) -> dict:
        cid = await ticket_mcp.handle_comment_on_ticket(
            tickets=tickets, comments=comments, bus=bus,
            sender=agent_role, sender_cfg=agent_cfg, args=args,
        )
        return {"content": [{"type": "text", "text": cid}]}

    @tool(
        name="list_tickets",
        description="List tickets, optionally filtered by type/status/assignee/parent_id",
        input_schema={
            "type": {"type": "string"},
            "status": {"type": "string"},
            "assignee": {"type": "string"},
            "parent_id": {"type": "string"},
        },
    )
    async def list_tickets(args: dict) -> dict:
        rows = await ticket_mcp.handle_list_tickets(tickets=tickets, args=args)
        return {"content": [{"type": "text", "text": "\n".join(r.model_dump_json() for r in rows)}]}

    @tool(
        name="read_comments",
        description="Read comments on a ticket, optionally filtered by kind",
        input_schema={
            "ticket_id": {"type": "string"},
            "kind": {"type": "string"},
        },
    )
    async def read_comments(args: dict) -> dict:
        rows = await ticket_mcp.handle_read_comments(
            comments=comments, ticket_id=args["ticket_id"], kind=args.get("kind"),
        )
        return {"content": [{"type": "text", "text": "\n".join(r.model_dump_json() for r in rows)}]}

    @tool(
        name="commit_progress",
        description="Commit staged changes in the ticket's worktree, record as a commit comment, publish to bus",
        input_schema={
            "ticket_id": {"type": "string"},
            "message": {"type": "string"},
        },
    )
    async def commit_progress(args: dict) -> dict:
        result = await ticket_mcp.handle_commit_progress(
            tickets=tickets, comments=comments, bus=bus,
            sender=agent_role, worktree_path=worktree_path, args=args,
        )
        return {"content": [{"type": "text", "text": str(result)}]}

    @tool(
        name="record_learning",
        description="Record a role-scoped learning that future spawns of this role will see",
        input_schema={"content": {"type": "string"}},
    )
    async def record_learning(args: dict) -> dict:
        msg = await ticket_mcp.handle_record_learning(
            memory=memory, role=agent_role, args=args,
        )
        return {"content": [{"type": "text", "text": msg}]}

    @tool(
        name="request_context",
        description="Read a file from the ticket's worktree",
        input_schema={"path": {"type": "string"}},
    )
    async def request_context(args: dict) -> dict:
        text = await ticket_mcp.handle_request_context(
            worktree_path=worktree_path, args=args,
        )
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(
        name="jig",
        tools=[
            create_ticket, read_ticket, update_ticket, comment_on_ticket,
            list_tickets, read_comments, commit_progress,
            record_learning, request_context,
        ],
    )
```

Note: the exact input_schema shape depends on the `claude_agent_sdk` version in use; match the existing working pattern in the old `jig/mcp_server.py` (JSON-Schema-style dict) if the version differs. Also delete `create_orchestrator_mcp_server` — it was never used in practice and the orchestrator no longer speaks MCP under the new design.

- [ ] **Step 4: Run the mcp server test**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jig/mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): rewire create_agent_mcp_server to ticket tool surface"
```

---

# Phase 3 — Environment context and jig skills library

### Task 3.1: Move skill files and add frontmatter

**Files:**
- Create: `jig/skills/__init__.py` (empty)
- Create: `jig/skills/jig-mcp-tools.md`
- Create: `jig/skills/git-conventions.md`
- Create: `jig/skills/python.md`
- Create: `jig/skills/uv.md`
- Create: `jig/skills/pytest.md`
- Create: `jig/skills/ruff.md`
- Create: `jig/skills/typescript.md`
- Create: `jig/skills/pnpm.md`
- Create: `jig/skills/vitest.md`

- [ ] **Step 1: Create the package marker**

Write `jig/skills/__init__.py`:

```python
"""Jig-shipped skills library. See jig/skill_loader.py for discovery."""
```

- [ ] **Step 2: Author the universal skills**

`jig/skills/jig-mcp-tools.md`:

```markdown
---
name: jig-mcp-tools
applies_to: {}
---

# Jig MCP tools

You have a `jig` MCP server. Use it for all work-tracking actions.

## Tools

- `create_ticket(type, title, description, assignee?, parent_id?)` — file a new ticket. Use type=question with assignee="user" to ask the user a clarifying question.
- `comment_on_ticket(ticket_id, content)` — post a comment. Use this to narrate progress, explain decisions, or reply to another agent.
- `update_ticket(ticket_id, status=...)` — transition ticket status. Use status=resolved when work is complete, blocked if waiting on external input, needs_info if waiting on a question.
- `commit_progress(ticket_id, message)` — git commit in the ticket worktree, file a commit comment, publish to the bus. Call this after every meaningful chunk of work.
- `record_learning(content)` — save a role-wide lesson you want to remember in future spawns.

## Rules

- Always `commit_progress` after a chunk of work lands. Small, frequent commits are preferred over one large one.
- When you're done, call `update_ticket(status="resolved")` on your primary ticket.
- To ask another role a question, use `create_ticket(type="question", assignee="<role>", parent_id=<current_ticket>, description="...")` and wait for the answer to arrive as a conversation turn.
```

`jig/skills/git-conventions.md`:

```markdown
---
name: git-conventions
applies_to: {}
---

# Git conventions

- Use conventional commits format: `<type>(<scope>): <subject>`. Types: feat, fix, chore, docs, test, refactor.
- Commit early and often via `commit_progress`. Never hand-commit outside the MCP tool.
- One logical change per commit. If the work spans multiple concerns, split it.
- Do not force-push, rebase, or rewrite history.
```

- [ ] **Step 3: Author the Python skills**

`jig/skills/python.md`:

```markdown
---
name: python
applies_to:
  language: python
---

# Python conventions

- Follow PEP 8. Prefer explicit over implicit.
- Use type hints on public function signatures.
- Use `pathlib.Path` for filesystem paths, not `os.path`.
- Use f-strings for formatting, not `%` or `.format()`.
```

`jig/skills/uv.md`:

```markdown
---
name: uv
applies_to:
  language: python
  package_manager: uv
---

# uv conventions

- Always run Python through `uv run python`, never bare `python`.
- Always run tests through `uv run pytest`, never bare `pytest`.
- Add dependencies with `uv add <pkg>`, never `pip install`.
- `uv.lock` is checked in; regenerate with `uv lock` if needed.
```

`jig/skills/pytest.md`:

```markdown
---
name: pytest
applies_to:
  language: python
---

# pytest conventions

- Test files live in `tests/` and are named `test_*.py`.
- Test functions start with `test_`.
- Use `pytest.fixture` for setup, not `unittest` classes.
- Use `pytest.mark.asyncio` for async tests (requires `pytest-asyncio`).
- Run a single test: `uv run pytest tests/test_foo.py::test_bar -v`.
```

`jig/skills/ruff.md`:

```markdown
---
name: ruff
applies_to:
  language: python
---

# ruff

- Format: `uv run ruff format .`
- Lint: `uv run ruff check .`
- Fix safely: `uv run ruff check --fix .`
- Configuration lives in `pyproject.toml` under `[tool.ruff]`.
```

- [ ] **Step 4: Author the TypeScript skills**

`jig/skills/typescript.md`:

```markdown
---
name: typescript
applies_to:
  language: typescript
---

# TypeScript conventions

- Prefer `const` over `let`; never use `var`.
- Use explicit return types on exported functions.
- Use `unknown` over `any` for untrusted values.
- Module format is ESM (`import`/`export`), not CommonJS (`require`).
```

`jig/skills/pnpm.md`:

```markdown
---
name: pnpm
applies_to:
  language: typescript
  package_manager: pnpm
---

# pnpm conventions

- Install deps: `pnpm install`.
- Add a dep: `pnpm add <pkg>`. Dev dep: `pnpm add -D <pkg>`.
- Run a script: `pnpm <script>` (not `pnpm run <script>`).
- `pnpm-lock.yaml` is checked in.
```

`jig/skills/vitest.md`:

```markdown
---
name: vitest
applies_to:
  language: typescript
---

# vitest conventions

- Test files are named `*.test.ts` and colocated with source, or live in `tests/`.
- Run all tests: `pnpm test`.
- Run a single file: `pnpm test path/to/file.test.ts`.
- Use `describe`/`it` for grouping; `expect(x).toBe(y)` for assertions.
```

- [ ] **Step 5: Commit**

```bash
git add jig/skills/
git commit -m "feat(skills): add jig-shipped skills library with frontmatter"
```

### Task 3.2: `SkillLoader` with match semantics

**Files:**
- Create: `jig/skill_loader.py`
- Test: `tests/test_skill_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skill_loader.py
from jig.project import Project
from jig.skill_loader import Skill, load_all_skills, match_skills


def test_load_all_skills_returns_every_skill() -> None:
    skills = load_all_skills()
    names = {s.name for s in skills}
    # Universal skills must be present
    assert "jig-mcp-tools" in names
    assert "git-conventions" in names
    # Python + TS skills must be present
    assert "python" in names
    assert "uv" in names
    assert "typescript" in names


def test_universal_skills_always_match() -> None:
    skills = load_all_skills()
    matched = match_skills(
        project=Project(id="x", name="x", path="/tmp", language="", package_manager=""),
        skills=skills,
    )
    names = {s.name for s in matched}
    assert "jig-mcp-tools" in names
    assert "git-conventions" in names


def test_python_uv_project_matches_uv_skill() -> None:
    skills = load_all_skills()
    project = Project(
        id="x", name="x", path="/tmp", language="python", package_manager="uv",
    )
    matched = match_skills(project=project, skills=skills)
    names = {s.name for s in matched}
    assert "python" in names
    assert "uv" in names
    assert "typescript" not in names
    assert "pnpm" not in names


def test_typescript_pnpm_project_matches_ts_skills() -> None:
    skills = load_all_skills()
    project = Project(
        id="x", name="x", path="/tmp", language="typescript", package_manager="pnpm",
    )
    matched = match_skills(project=project, skills=skills)
    names = {s.name for s in matched}
    assert "typescript" in names
    assert "pnpm" in names
    assert "vitest" in names
    assert "python" not in names
    assert "uv" not in names


def test_match_is_filename_sorted() -> None:
    skills = load_all_skills()
    project = Project(
        id="x", name="x", path="/tmp", language="python", package_manager="uv",
    )
    matched = match_skills(project=project, skills=skills)
    # filename sort is deterministic, not alphabetical-on-name
    sources = [s.source_filename for s in matched]
    assert sources == sorted(sources)


def test_skill_content_contains_frontmatter_stripped() -> None:
    skills = load_all_skills()
    python_skill = next(s for s in skills if s.name == "python")
    assert "---" not in python_skill.content.split("\n")[0]
    assert "# Python conventions" in python_skill.content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_skill_loader.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jig.skill_loader'`

- [ ] **Step 3: Write minimal implementation**

```python
# jig/skill_loader.py
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources

import yaml

from jig.project import Project


@dataclass(frozen=True)
class Skill:
    name: str
    source_filename: str
    applies_to: dict[str, str]
    content: str


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body. Returns (frontmatter_dict, body)."""
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return {}, raw
    front = raw[4:end]
    body = raw[end + len("\n---\n"):].lstrip("\n")
    data = yaml.safe_load(front) or {}
    return data, body


def load_all_skills() -> list[Skill]:
    """Load every skill shipped under jig/skills/, filename-sorted."""
    pkg = resources.files("jig.skills")
    results: list[Skill] = []
    for entry in sorted(pkg.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".md"):
            continue
        raw = entry.read_text(encoding="utf-8")
        front, body = _parse_frontmatter(raw)
        name = front.get("name", entry.name.removesuffix(".md"))
        applies_to = front.get("applies_to") or {}
        results.append(Skill(
            name=name,
            source_filename=entry.name,
            applies_to=applies_to,
            content=body,
        ))
    return results


def match_skills(*, project: Project, skills: list[Skill]) -> list[Skill]:
    """Return skills whose applies_to block matches the project. Filename-sorted."""
    matched: list[Skill] = []
    for skill in skills:
        if _matches(skill.applies_to, project):
            matched.append(skill)
    return matched


def _matches(applies_to: dict[str, str], project: Project) -> bool:
    if not applies_to:
        return True
    for key, expected in applies_to.items():
        actual = getattr(project, key, None)
        if actual != expected:
            return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_skill_loader.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Ensure skills are packaged**

Open `pyproject.toml`. Confirm `jig/skills/*.md` is included in the package data. If not, add:

```toml
[tool.hatch.build.targets.wheel.force-include]
"jig/skills" = "jig/skills"
```

or the equivalent for whichever build backend is in use. Run `uv build` and inspect the wheel to confirm.

- [ ] **Step 6: Commit**

```bash
git add jig/skill_loader.py tests/test_skill_loader.py pyproject.toml
git commit -m "feat(skills): add skill_loader with frontmatter match semantics"
```

### Task 3.3: `environment.md` loader

**Files:**
- Create: `jig/environment.py`
- Test: `tests/test_environment.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_environment.py
from pathlib import Path

from jig.environment import load_environment_md


def test_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_environment_md(tmp_path) == ""


def test_returns_verbatim_when_present(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    content = "## Quirks\n\n- Never run bare pytest\n"
    (tmp_path / ".jig" / "environment.md").write_text(content)
    assert load_environment_md(tmp_path) == content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_environment.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jig.environment'`

- [ ] **Step 3: Write minimal implementation**

```python
# jig/environment.py
from pathlib import Path


def load_environment_md(project_path: Path) -> str:
    """Load .jig/environment.md verbatim, or return '' if absent."""
    path = project_path / ".jig" / "environment.md"
    if not path.is_file():
        return ""
    return path.read_text()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_environment.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/environment.py tests/test_environment.py
git commit -m "feat(environment): add load_environment_md"
```

### Task 3.4: `prompt_builder.build_initial_prompt`

**Files:**
- Create: `jig/prompt_builder.py`
- Test: `tests/test_prompt_builder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_prompt_builder.py
from jig.models import AgentTypeConfig
from jig.project import Project
from jig.prompt_builder import SpawnReason, build_initial_prompt
from jig.skill_loader import Skill
from jig.ticket import Comment, Ticket, TicketStatus, TicketType


def _project() -> Project:
    return Project(
        id="p", name="p", path="/tmp",
        language="python", package_manager="uv",
        test_command="uv run pytest",
    )


def _cfg() -> AgentTypeConfig:
    return AgentTypeConfig(role="dev", phase_prompt="You are dev.", response_prompt="You answer.")


def _ticket() -> Ticket:
    return Ticket(
        type=TicketType.TASK, title="implement X", created_by="orchestrator",
        description="do the thing", status=TicketStatus.OPEN,
    )


def test_injection_order() -> None:
    parent = Ticket(
        type=TicketType.FEATURE, title="parent", created_by="user",
        description="overall goal",
    )
    uv_skill = Skill(
        name="uv", source_filename="uv.md", applies_to={},
        content="# uv\n\nalways uv run",
    )
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=parent,
        comments=[],
        memories=["use pytest-asyncio"],
        project=_project(),
        skills=[uv_skill],
        environment_md="## Env\nnever touch legacy/\n",
    )
    # Check the order by substring position
    assert prompt.index("You are dev.") < prompt.index("## Project Context")
    assert prompt.index("## Project Context") < prompt.index("# uv")
    assert prompt.index("# uv") < prompt.index("## Env")
    assert prompt.index("## Env") < prompt.index("use pytest-asyncio")
    assert prompt.index("use pytest-asyncio") < prompt.index("implement X")
    assert "overall goal" in prompt  # parent description included


def test_qa_responder_uses_response_prompt() -> None:
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.QA_RESPONDER,
        ticket=_ticket(),
        parent=None,
        comments=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "You answer." in prompt
    assert "You are dev." not in prompt


def test_qa_responder_falls_back_to_phase_prompt_with_preamble() -> None:
    cfg = AgentTypeConfig(role="dev", phase_prompt="You are dev.")  # no response_prompt
    prompt = build_initial_prompt(
        role_cfg=cfg,
        spawn_reason=SpawnReason.QA_RESPONDER,
        ticket=_ticket(),
        parent=None,
        comments=[],
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "answering a question" in prompt.lower()
    assert "You are dev." in prompt


def test_parent_comments_included() -> None:
    parent = Ticket(type=TicketType.FEATURE, title="p", created_by="u", description="")
    parent_comments = [
        Comment(ticket_id="parent-id", author="spec-writer", content="use redis"),
        Comment(ticket_id="parent-id", author="spec-writer", content="index by id"),
    ]
    prompt = build_initial_prompt(
        role_cfg=_cfg(),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=_ticket(),
        parent=parent,
        comments=parent_comments,
        memories=[],
        project=_project(),
        skills=[],
        environment_md="",
    )
    assert "use redis" in prompt
    assert "index by id" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompt_builder.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'jig.prompt_builder'`

- [ ] **Step 3: Write minimal implementation**

```python
# jig/prompt_builder.py
from enum import Enum

from jig.models import AgentTypeConfig
from jig.project import Project
from jig.skill_loader import Skill
from jig.ticket import Comment, Ticket


class SpawnReason(str, Enum):
    PHASE_PRIMARY = "phase_primary"
    QA_RESPONDER = "qa_responder"


def _role_section(cfg: AgentTypeConfig, reason: SpawnReason) -> str:
    if reason == SpawnReason.QA_RESPONDER:
        if cfg.response_prompt:
            return cfg.response_prompt + "\n\n"
        return (
            "You are answering a question from another role. Below is your "
            f"normal role prompt for context.\n\n{cfg.phase_prompt}\n\n"
        )
    return cfg.phase_prompt + "\n\n"


def _project_section(p: Project) -> str:
    lines = ["## Project Context\n"]
    if p.name:
        lines.append(f"- **Project**: {p.name}")
    if p.description:
        lines.append(f"- **Description**: {p.description}")
    if p.language:
        lines.append(f"- **Language**: {p.language}")
    if p.framework:
        lines.append(f"- **Framework**: {p.framework}")
    if p.package_manager:
        lines.append(f"- **Package manager**: {p.package_manager}")
    if p.test_command:
        lines.append(f"- **Test**: `{p.test_command}`")
    if p.build_command:
        lines.append(f"- **Build**: `{p.build_command}`")
    lines.append(f"- **Default branch**: {p.default_branch}")
    return "\n".join(lines) + "\n\n"


def _skills_section(skills: list[Skill]) -> str:
    if not skills:
        return ""
    parts = ["## Skills\n"]
    for s in skills:
        parts.append(s.content.rstrip() + "\n")
    return "\n".join(parts) + "\n"


def _environment_section(env_md: str) -> str:
    if not env_md.strip():
        return ""
    return f"## Environment\n\n{env_md.rstrip()}\n\n"


def _memories_section(memories: list[str]) -> str:
    if not memories:
        return ""
    lines = ["## Your Memories\n"]
    lines.extend(f"- {m}" for m in memories)
    return "\n".join(lines) + "\n\n"


def _ticket_section(
    ticket: Ticket, parent: Ticket | None, comments: list[Comment]
) -> str:
    parts = [f"## Ticket: {ticket.title}\n"]
    if ticket.description:
        parts.append(ticket.description)
    if parent is not None:
        parts.append(f"\n### Parent: {parent.title}\n")
        if parent.description:
            parts.append(parent.description)
    if comments:
        parts.append("\n### Relevant comments\n")
        for c in comments:
            parts.append(f"- [{c.author}] {c.content}")
    return "\n".join(parts) + "\n\n"


def _instructions_section(ticket: Ticket, reason: SpawnReason) -> str:
    if reason == SpawnReason.QA_RESPONDER:
        return (
            "## Instructions\n\n"
            f"Respond to the most recent message on ticket {ticket.id}. "
            "When you've answered, call "
            f"`update_ticket(ticket_id=\"{ticket.id}\", status=\"resolved\")`.\n"
        )
    return (
        "## Instructions\n\n"
        f"Work on ticket {ticket.id}. Call `commit_progress` after each "
        "meaningful chunk of work. When the task is complete, call "
        f"`update_ticket(ticket_id=\"{ticket.id}\", status=\"resolved\")`. "
        "If blocked or in need of clarification, set status=\"blocked\" or "
        "status=\"needs_info\" and explain via `comment_on_ticket`.\n"
    )


def build_initial_prompt(
    *,
    role_cfg: AgentTypeConfig,
    spawn_reason: SpawnReason,
    ticket: Ticket,
    parent: Ticket | None,
    comments: list[Comment],
    memories: list[str],
    project: Project,
    skills: list[Skill],
    environment_md: str,
) -> str:
    parts = [
        _role_section(role_cfg, spawn_reason),
        _project_section(project),
        _skills_section(skills),
        _environment_section(environment_md),
        _memories_section(memories),
        _ticket_section(ticket, parent, comments),
        _instructions_section(ticket, spawn_reason),
    ]
    return "".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prompt_builder.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat(prompt): add build_initial_prompt with layered injection"
```

---

# Phase 4 — Agent runtime (streaming input mode)

### Task 4.1: `SpawnReason` + `AgentSpawnContext` module

**Files:**
- Create: `jig/runtime.py`

- [ ] **Step 1: Move `SpawnReason` out of `prompt_builder` and into `runtime`**

This avoids a circular import once `agent.py` imports both prompt_builder and runtime.

Create `jig/runtime.py`:

```python
# jig/runtime.py
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from jig.models import AgentTypeConfig
from jig.project import Project
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket


class SpawnReason(str, Enum):
    PHASE_PRIMARY = "phase_primary"
    QA_RESPONDER = "qa_responder"


@dataclass
class AgentSpawnContext:
    role: str
    role_cfg: AgentTypeConfig
    spawn_reason: SpawnReason
    ticket: Ticket
    parent: Ticket | None
    worktree_path: Path
    project: Project
    tickets: TicketStore
    comments: CommentStore
    memory: MemoryStore
    bus: MessageBus
    initial_bus_message: dict | None = None
```

- [ ] **Step 2: Update `prompt_builder` to re-export `SpawnReason` from `runtime`**

Replace the `SpawnReason` class in `jig/prompt_builder.py` with:

```python
from jig.runtime import SpawnReason  # re-export for callers that already import from prompt_builder
```

- [ ] **Step 3: Run prompt_builder tests to confirm no regression**

Run: `uv run pytest tests/test_prompt_builder.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add jig/runtime.py jig/prompt_builder.py
git commit -m "feat(runtime): add SpawnReason and AgentSpawnContext dataclass"
```

### Task 4.2: Rewrite `run_agent` for streaming input mode

**Files:**
- Modify: `jig/agent.py` (full rewrite of `run_agent`)
- Test: `tests/test_agent_streaming.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_agent_streaming.py
"""Unit tests for run_agent under streaming input mode.

These tests mock the claude_agent_sdk.query generator so we don't actually
call into the real SDK. We verify that:
  - the initial prompt is built via prompt_builder
  - bus events addressed to this agent become conversation turns
  - a terminal update_ticket(status=resolved) closes the generator
"""
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jig.models import AgentTypeConfig
from jig.project import Project
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketType


async def _make_context(tmp_path: Path) -> AgentSpawnContext:
    tickets = TicketStore(tmp_path / "tickets.jsonl"); await tickets.load()
    comments = CommentStore(tmp_path / "comments.jsonl"); await comments.load()
    memory = MemoryStore(tmp_path / "memories.jsonl"); await memory.load()
    bus = MessageBus(tmp_path / "messages.jsonl"); await bus.load()
    t = Ticket(type=TicketType.TASK, title="t", created_by="o", description="do it")
    tid = await tickets.create(t)
    loaded = await tickets.get(tid)
    return AgentSpawnContext(
        role="dev",
        role_cfg=AgentTypeConfig(role="dev", phase_prompt="be dev"),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=loaded,
        parent=None,
        worktree_path=tmp_path / "worktree",
        project=Project(id="p", name="p", path=str(tmp_path), language="python", package_manager="uv"),
        tickets=tickets,
        comments=comments,
        memory=memory,
        bus=bus,
    )


@pytest.mark.asyncio
async def test_run_agent_builds_initial_prompt(tmp_path: Path) -> None:
    ctx = await _make_context(tmp_path)

    captured_prompt_iter = None

    async def fake_query(prompt, options):
        nonlocal captured_prompt_iter
        captured_prompt_iter = prompt
        # Consume one turn, then end.
        async for turn in prompt:
            yield _fake_result_message()
            return

    from jig import agent as agent_module
    with patch.object(agent_module, "query", fake_query):
        await agent_module.run_agent(ctx)

    assert captured_prompt_iter is not None


@pytest.mark.asyncio
async def test_run_agent_yields_incoming_bus_events(tmp_path: Path) -> None:
    ctx = await _make_context(tmp_path)

    seen_turns: list[str] = []

    async def fake_query(prompt, options):
        async for turn in prompt:
            seen_turns.append(turn if isinstance(turn, str) else str(turn))
            if len(seen_turns) >= 2:
                yield _fake_result_message()
                return

    async def publish_delayed():
        import asyncio
        await asyncio.sleep(0.05)
        from jig.store import Message, MessageType
        await ctx.bus.publish(Message(
            sender="qa",
            to="dev",
            type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "comment_posted", "content": "did you handle edge X?"},
            topic=f"tickets.{ctx.ticket.id}",
        ))
        # Then publish a terminal event (comment from ourselves resolving the ticket)
        await ctx.bus.publish(Message(
            sender="dev",
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "ticket_updated", "ticket_id": ctx.ticket.id, "status": "resolved"},
            topic=f"tickets.{ctx.ticket.id}",
        ))

    from jig import agent as agent_module
    import asyncio
    with patch.object(agent_module, "query", fake_query):
        await asyncio.gather(
            agent_module.run_agent(ctx),
            publish_delayed(),
        )

    # First turn is the initial prompt; second turn is the bus-delivered comment.
    assert len(seen_turns) == 2
    assert "did you handle edge X" in seen_turns[1]


def _fake_result_message():
    class _M:
        result = "done"
        num_turns = 1
        duration_ms = 1
        total_cost_usd = 0.0
    return _M()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_streaming.py -v`
Expected: FAIL, `run_agent` has wrong signature or module patching fails.

- [ ] **Step 3: Rewrite `run_agent`**

Replace the existing body of `jig/agent.py` (keep the helpers `_sanitize_for_tui`, `_tool_detail`). Core new body:

```python
# jig/agent.py — replace run_agent and imports
import asyncio
import logging
from dataclasses import dataclass

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, ResultMessage, SystemMessage

from jig.environment import load_environment_md
from jig.mcp_server import create_agent_mcp_server
from jig.prompt_builder import build_initial_prompt
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.skill_loader import load_all_skills, match_skills
from jig.store import Message
from jig.ticket import TicketStatus

_logger = logging.getLogger(__name__)


@dataclass
class RunAgentResult:
    status: str  # "success" | "failed" | "blocked" | "needs_info"
    final_text: str


async def run_agent(ctx: AgentSpawnContext) -> RunAgentResult:
    """Run a Claude agent against a ticket in streaming input mode.

    Builds the initial prompt, subscribes to the ticket's bus topic, yields
    incoming bus events (addressed to this role or broadcast) as user turns,
    and terminates when the agent sets the primary ticket to a terminal
    status (resolved, blocked, needs_info).
    """
    skills = match_skills(project=ctx.project, skills=load_all_skills())
    env_md = load_environment_md(ctx.project.path_or_default())
    memories = [l.content for l in await ctx.memory.get_role_learnings(ctx.role)]
    comments = (
        await ctx.comments.for_ticket(ctx.parent.id) if ctx.parent else []
    )

    initial_prompt = build_initial_prompt(
        role_cfg=ctx.role_cfg,
        spawn_reason=ctx.spawn_reason,
        ticket=ctx.ticket,
        parent=ctx.parent,
        comments=comments,
        memories=memories,
        project=ctx.project,
        skills=skills,
        environment_md=env_md,
    )

    mcp_server = create_agent_mcp_server(
        tickets=ctx.tickets,
        comments=ctx.comments,
        memory=ctx.memory,
        bus=ctx.bus,
        agent_role=ctx.role,
        agent_cfg=ctx.role_cfg,
        worktree_path=ctx.worktree_path,
    )

    options = ClaudeAgentOptions(
        cwd=str(ctx.worktree_path),
        allowed_tools=ctx.role_cfg.allowed_tools,
        disallowed_tools=[],
        system_prompt=ctx.role_cfg.phase_prompt,
        mcp_servers={"jig": mcp_server},
        permission_mode="bypassPermissions",
    )

    bus_queue = await ctx.bus.subscribe_agent(
        topic=f"tickets.{ctx.ticket.id}", agent_id=f"{ctx.role}:{ctx.ticket.id}"
    )
    terminal_statuses = {
        TicketStatus.RESOLVED, TicketStatus.BLOCKED, TicketStatus.NEEDS_INFO
    }
    done = asyncio.Event()

    async def _prompt_stream():
        yield initial_prompt
        while not done.is_set():
            try:
                msg = await asyncio.wait_for(bus_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                # Periodic check: has the ticket reached a terminal status?
                current = await ctx.tickets.get(ctx.ticket.id)
                if current and current.status in terminal_statuses:
                    done.set()
                continue
            if not _is_relevant(msg, ctx):
                continue
            yield _format_bus_event(msg)
            # After yielding, check if this was a terminal status update
            payload = msg.payload or {}
            if (
                payload.get("kind") == "ticket_updated"
                and payload.get("ticket_id") == ctx.ticket.id
                and payload.get("status") in {s.value for s in terminal_statuses}
            ):
                done.set()

    final_text = ""
    status = "success"
    try:
        async for message in query(prompt=_prompt_stream(), options=options):
            if isinstance(message, ResultMessage):
                final_text = message.result or ""
    finally:
        done.set()

    current = await ctx.tickets.get(ctx.ticket.id)
    if current is not None:
        status = _status_to_result(current.status)

    return RunAgentResult(status=status, final_text=final_text)


def _is_relevant(msg: Message, ctx: AgentSpawnContext) -> bool:
    return msg.to in (ctx.role, "broadcast") and msg.sender != ctx.role


def _format_bus_event(msg: Message) -> str:
    payload = msg.payload or {}
    kind = payload.get("kind", "event")
    if kind == "comment_posted":
        return (
            f"[comment from {payload.get('author')} on ticket "
            f"{payload.get('ticket_id')}]: {payload.get('content')}"
        )
    if kind == "ticket_created":
        return f"[new ticket {payload.get('ticket_id')} assigned to you]"
    return f"[{kind}] {payload}"


def _status_to_result(status: TicketStatus) -> str:
    if status == TicketStatus.RESOLVED:
        return "success"
    if status == TicketStatus.BLOCKED:
        return "blocked"
    if status == TicketStatus.NEEDS_INFO:
        return "needs_info"
    if status == TicketStatus.FAILED:
        return "failed"
    return "success"  # still in progress — treat as success for now
```

Also add to `jig/project.py`:

```python
from pathlib import Path

class Project(BaseModel):
    # ... existing fields ...

    def path_or_default(self) -> Path:
        return Path(self.path)
```

- [ ] **Step 4: Run the agent streaming tests**

Run: `uv run pytest tests/test_agent_streaming.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/agent.py jig/project.py tests/test_agent_streaming.py
git commit -m "feat(agent): rewrite run_agent for streaming input mode"
```

---

# Phase 5 — Orchestrator rewrite

### Task 5.1: Orchestrator scaffolding + service loop

**Files:**
- Rewrite: `jig/orchestrator.py`
- Test: `tests/test_orchestrator_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_dispatch.py
import asyncio
from pathlib import Path

import pytest

from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ticket import TicketType, TicketStatus


@pytest.mark.asyncio
async def test_orchestrator_startup_loads_collections(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(id="p", name="p", path=str(tmp_path), language="python", package_manager="uv"),
    )
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        assert orch.tickets is not None
        assert orch.comments is not None
        assert orch.bus is not None
        assert orch._live_subscribers == {}
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_resumes_in_progress_tickets(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(id="p", name="p", path=str(tmp_path), language="python", package_manager="uv"),
    )
    # Pre-populate a feature ticket in IN_PROGRESS
    from jig.store.tickets import TicketStore
    from jig.ticket import Ticket
    ts = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await ts.load()
    tid = await ts.create(Ticket(
        type=TicketType.FEATURE, title="f", created_by="user",
        status=TicketStatus.IN_PROGRESS,
    ))

    orch = Orchestrator(project_path=tmp_path)
    # Stub out _run_ticket so we just observe which tickets it got called with
    seen: list[str] = []
    async def fake_run_ticket(ticket_id: str) -> None:
        seen.append(ticket_id)
    orch._run_ticket = fake_run_ticket  # type: ignore

    await orch.startup()
    try:
        # Give the resume scan a moment to kick off tasks
        await asyncio.sleep(0.05)
        assert seen == [tid]
    finally:
        await orch.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator_dispatch.py -v`
Expected: FAIL, `Orchestrator.__init__` takes wrong args or class doesn't exist in new shape.

- [ ] **Step 3: Replace `jig/orchestrator.py` with the new scaffolding**

```python
# jig/orchestrator.py
import asyncio
import logging
from pathlib import Path

from jig.project import Project, load_project
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore
from jig.ticket import TicketStatus, TicketType

_logger = logging.getLogger(__name__)


class Orchestrator:
    """Singleton orchestrator per project process.

    Runs three concurrent asyncio loops over the shared bus and stores:
      - service loop: external wake-ups on the "orchestrator" topic
      - per-ticket loops: one _run_ticket task per in-flight top-level ticket
      - dispatch loop: spawns fresh agents for unaddressed bus events
    """

    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path
        self._project: Project | None = None
        self.tickets: TicketStore | None = None
        self.comments: CommentStore | None = None
        self.memory: MemoryStore | None = None
        self.bus: MessageBus | None = None

        self._running_tickets: dict[str, asyncio.Task] = {}
        self._live_subscribers: dict[tuple[str, str], asyncio.Task] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._service_task: asyncio.Task | None = None
        self._running = False

    async def startup(self) -> None:
        self._project = load_project(self._project_path)
        store_dir = self._project_path / ".jig" / "store"
        self.tickets = TicketStore(store_dir / "tickets.jsonl")
        self.comments = CommentStore(store_dir / "comments.jsonl")
        self.memory = MemoryStore(store_dir / "memories.jsonl")
        self.bus = MessageBus(store_dir / "messages.jsonl")
        await asyncio.gather(
            self.tickets.load(),
            self.comments.load(),
            self.memory.load(),
            self.bus.load(),
        )
        self._running = True
        self._dispatch_task = asyncio.create_task(self._run_dispatch_loop())
        self._service_task = asyncio.create_task(self._run_service_loop())
        await self._resume_in_progress()

    async def shutdown(self) -> None:
        self._running = False
        tasks_to_cancel: list[asyncio.Task] = []
        if self._dispatch_task:
            tasks_to_cancel.append(self._dispatch_task)
        if self._service_task:
            tasks_to_cancel.append(self._service_task)
        tasks_to_cancel.extend(self._running_tickets.values())
        tasks_to_cancel.extend(self._live_subscribers.values())
        for t in tasks_to_cancel:
            t.cancel()
        for t in tasks_to_cancel:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._running_tickets.clear()
        self._live_subscribers.clear()

    async def _resume_in_progress(self) -> None:
        in_progress = await self.tickets.find_in_progress_top_level()
        for ticket in in_progress:
            task = asyncio.create_task(self._run_ticket(ticket.id))
            self._running_tickets[ticket.id] = task

    async def _run_service_loop(self) -> None:
        queue = await self.bus.subscribe("orchestrator")
        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            payload = msg.payload or {}
            kind = payload.get("kind")
            if kind == "ticket_created":
                await self._handle_schedule(payload["ticket_id"])
            elif kind == "shutdown_request":
                self._running = False

    async def _handle_schedule(self, ticket_id: str) -> None:
        """For MVP: immediately start the ticket. _decide_scheduling is a stub."""
        if ticket_id in self._running_tickets:
            return
        await self.tickets.update_status(ticket_id, TicketStatus.IN_PROGRESS)
        task = asyncio.create_task(self._run_ticket(ticket_id))
        self._running_tickets[ticket_id] = task

    async def _run_ticket(self, ticket_id: str) -> None:
        """Per-ticket loop. Task 5.3 fills this in."""
        _logger.info("run_ticket stub: %s", ticket_id)

    async def _run_dispatch_loop(self) -> None:
        """Dispatch loop. Task 5.2 fills this in."""
        while self._running:
            await asyncio.sleep(0.1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator_dispatch.py::test_orchestrator_startup_loads_collections tests/test_orchestrator_dispatch.py::test_orchestrator_resumes_in_progress_tickets -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator_dispatch.py
git commit -m "feat(orchestrator): add scaffolding with service loop and resume scan"
```

### Task 5.2: Dispatch loop

**Files:**
- Modify: `jig/orchestrator.py`
- Modify: `tests/test_orchestrator_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_dispatch_loop_spawns_for_unaddressed_message(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(id="p", name="p", path=str(tmp_path), language="python", package_manager="uv"),
    )
    from jig.ticket import Ticket
    from jig.store.tickets import TicketStore
    ts = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await ts.load()
    tid = await ts.create(Ticket(
        type=TicketType.QUESTION, title="q", created_by="dev",
        assignee="spec-writer", parent_id="parent-1",
    ))

    orch = Orchestrator(project_path=tmp_path)
    spawn_calls: list[tuple[str, str]] = []
    async def fake_spawn(ticket_id: str, role: str, initial_event) -> None:
        spawn_calls.append((ticket_id, role))
    orch._spawn_qa_responder = fake_spawn  # type: ignore

    await orch.startup()
    try:
        # Publish a ticket-topic event addressed to spec-writer on this ticket
        from jig.store import Message, MessageType
        await orch.bus.publish(Message(
            sender="dev", to="spec-writer", type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "ticket_created", "ticket_id": tid},
            topic=f"tickets.{tid}",
        ))
        await asyncio.sleep(0.1)
        assert (tid, "spec-writer") in spawn_calls
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_dispatch_loop_skips_user_role(tmp_path: Path) -> None:
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    from jig.ticket import Ticket
    from jig.store.tickets import TicketStore
    ts = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await ts.load()
    tid = await ts.create(Ticket(
        type=TicketType.QUESTION, title="q", created_by="dev", assignee="user",
    ))

    orch = Orchestrator(project_path=tmp_path)
    spawn_calls: list[tuple[str, str]] = []
    async def fake_spawn(ticket_id: str, role: str, initial_event) -> None:
        spawn_calls.append((ticket_id, role))
    orch._spawn_qa_responder = fake_spawn  # type: ignore

    await orch.startup()
    try:
        from jig.store import Message, MessageType
        await orch.bus.publish(Message(
            sender="dev", to="user", type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "ticket_created", "ticket_id": tid},
            topic=f"tickets.{tid}",
        ))
        await asyncio.sleep(0.1)
        assert spawn_calls == []  # user is never spawned
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_dispatch_loop_skips_if_live_subscriber_present(tmp_path: Path) -> None:
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    from jig.ticket import Ticket
    from jig.store.tickets import TicketStore
    ts = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await ts.load()
    tid = await ts.create(Ticket(
        type=TicketType.TASK, title="t", created_by="o", assignee="dev",
    ))

    orch = Orchestrator(project_path=tmp_path)
    spawn_calls: list[tuple[str, str]] = []
    async def fake_spawn(ticket_id: str, role: str, initial_event) -> None:
        spawn_calls.append((ticket_id, role))
    orch._spawn_qa_responder = fake_spawn  # type: ignore

    await orch.startup()
    try:
        # Pretend a dev agent is already live on this ticket
        fake_task = asyncio.create_task(asyncio.sleep(60))
        orch._live_subscribers[(tid, "dev")] = fake_task
        from jig.store import Message, MessageType
        await orch.bus.publish(Message(
            sender="qa", to="dev", type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "comment_posted"},
            topic=f"tickets.{tid}",
        ))
        await asyncio.sleep(0.1)
        fake_task.cancel()
        assert spawn_calls == []
    finally:
        await orch.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator_dispatch.py -v`
Expected: FAIL, dispatch loop not implemented.

- [ ] **Step 3: Implement the dispatch loop**

In `jig/orchestrator.py`, replace `_run_dispatch_loop` with:

```python
    async def _run_dispatch_loop(self) -> None:
        # Subscribe to a wildcard topic — under the current MessageBus API
        # we subscribe per-topic, so we subscribe to all tickets.* patterns
        # by listening for new-topic events. For MVP we subscribe to a
        # special "all" listener via add_websocket_listener-style hook.
        queue: asyncio.Queue = asyncio.Queue()

        async def _forward(message) -> None:
            await queue.put(message)

        await self.bus.add_websocket_listener(_forward)

        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            target = self._resolve_target(msg)
            if target is None:
                continue
            ticket_id, role = target
            if role == "user":
                continue
            if (ticket_id, role) in self._live_subscribers:
                continue
            await self._spawn_qa_responder(ticket_id, role, msg)

    def _resolve_target(self, msg) -> tuple[str, str] | None:
        if not msg.topic.startswith("tickets."):
            return None
        ticket_id = msg.topic.removeprefix("tickets.")
        if msg.to == "broadcast" or not msg.to:
            return None
        return (ticket_id, msg.to)

    async def _spawn_qa_responder(
        self, ticket_id: str, role: str, initial_event
    ) -> None:
        # Full implementation lives in Task 5.4. For now, register a stub
        # task so the dispatch loop's "already subscribed" check works.
        async def _noop() -> None:
            return
        task = asyncio.create_task(_noop())
        self._live_subscribers[(ticket_id, role)] = task
        task.add_done_callback(
            lambda _: self._live_subscribers.pop((ticket_id, role), None)
        )
```

Note: the test overrides `_spawn_qa_responder`, so the stub body is fine for these three tests.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator_dispatch.py -v`
Expected: PASS (5 tests total — 2 startup + 3 dispatch)

- [ ] **Step 5: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator_dispatch.py
git commit -m "feat(orchestrator): implement dispatch loop with user-skip and live-subscriber check"
```

### Task 5.3: Per-ticket loop (happy path only, no recovery LLM)

**Files:**
- Modify: `jig/orchestrator.py`
- Test: `tests/test_orchestrator_per_ticket.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_per_ticket.py
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jig.models import AgentTypeConfig, PhaseConfig, WorkflowConfig
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ticket import Ticket, TicketStatus, TicketType


@pytest.mark.asyncio
async def test_per_ticket_loop_walks_phases_to_resolved(tmp_path: Path, monkeypatch) -> None:
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path), language="python", package_manager="uv"))

    # Workflow: spec -> dev -> qa
    wf = WorkflowConfig(name="default", phases=[
        PhaseConfig(name="spec", role="spec-writer"),
        PhaseConfig(name="dev", role="dev"),
        PhaseConfig(name="qa", role="qa"),
    ])
    # Pre-save workflow and agent type configs so the orchestrator can load them
    from jig.persistence import save_agent_type, save_workflow
    (tmp_path / ".jig" / "workflows").mkdir(parents=True)
    (tmp_path / ".jig" / "agent_types").mkdir()
    save_workflow(tmp_path, wf)
    for role in ("spec-writer", "dev", "qa"):
        save_agent_type(tmp_path, AgentTypeConfig(role=role, phase_prompt=f"be {role}"))

    orch = Orchestrator(project_path=tmp_path)

    # Stub run_agent to report success for every phase
    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    run_calls: list[str] = []
    async def fake_run_agent(ctx):
        run_calls.append(ctx.role)
        # Mark the task ticket as resolved so _run_ticket advances
        await ctx.tickets.update_status(ctx.ticket.id, TicketStatus.RESOLVED)
        return RunAgentResult(status="success", final_text="ok")
    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    # Stub worktree creation to just return a path
    async def fake_ensure(ticket):
        return tmp_path / "worktree"
    orch._ensure_worktree = fake_ensure  # type: ignore

    await orch.startup()
    try:
        # Create the feature ticket and schedule it
        tid = await orch.tickets.create(Ticket(
            type=TicketType.FEATURE, title="f", created_by="user",
        ))
        await orch._handle_schedule(tid)
        # Wait for _run_ticket to walk all phases
        for _ in range(40):
            await asyncio.sleep(0.05)
            current = await orch.tickets.get(tid)
            if current.status == TicketStatus.RESOLVED:
                break
        assert run_calls == ["spec-writer", "dev", "qa"]
        assert (await orch.tickets.get(tid)).status == TicketStatus.RESOLVED
    finally:
        await orch.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator_per_ticket.py -v`
Expected: FAIL, `_run_ticket` is a stub.

- [ ] **Step 3: Implement `_run_ticket`**

In `jig/orchestrator.py`:

```python
    async def _run_ticket(self, ticket_id: str) -> None:
        from jig.agent import run_agent  # local import avoids circular
        from jig.persistence import load_agent_type, load_workflow
        from jig.runtime import AgentSpawnContext, SpawnReason
        from jig.ticket import Ticket

        ticket = await self.tickets.get(ticket_id)
        if ticket is None:
            return
        workflow = load_workflow(self._project_path, "default")
        worktree = await self._ensure_worktree(ticket)
        phase_idx = await self._current_phase_index(ticket_id, workflow)

        while phase_idx < len(workflow.phases):
            phase = workflow.phases[phase_idx]
            role_cfg = load_agent_type(self._project_path, phase.role)

            task_ticket = Ticket(
                type=TicketType.TASK,
                title=f"{phase.name}: {ticket.title}",
                description=phase.task_template or ticket.description,
                parent_id=ticket_id,
                assignee=phase.role,
                created_by="orchestrator",
                status=TicketStatus.IN_PROGRESS,
            )
            task_id = await self.tickets.create(task_ticket)
            task_ticket = await self.tickets.get(task_id)

            ctx = AgentSpawnContext(
                role=phase.role,
                role_cfg=role_cfg,
                spawn_reason=SpawnReason.PHASE_PRIMARY,
                ticket=task_ticket,
                parent=ticket,
                worktree_path=worktree,
                project=self._project,
                tickets=self.tickets,
                comments=self.comments,
                memory=self.memory,
                bus=self.bus,
            )
            result = await run_agent(ctx)
            await self._write_phase_run_comment(task_id, phase, result)

            if result.status == "success":
                phase_idx += 1
                continue
            # MVP: fail fast on any non-success result. Crash/failure
            # recovery (spec §"Crash Recovery") is deferred post-MVP;
            # resumption is covered implicitly because parent-comment
            # history is injected into the next agent's initial prompt.
            await self.tickets.update_status(ticket_id, TicketStatus.FAILED)
            return

        await self.tickets.update_status(ticket_id, TicketStatus.RESOLVED)

    async def _ensure_worktree(self, ticket):
        from jig.worktree import create_worktree
        worktree_path = self._project_path / ".jig" / "worktrees" / ticket.id
        if worktree_path.exists():
            return worktree_path
        return await create_worktree(
            project_path=self._project_path,
            ticket_id=ticket.id,
            base_branch=self._project.default_branch,
        )

    async def _current_phase_index(self, ticket_id: str, workflow) -> int:
        """Count successful phase_run comments on task tickets under this ticket."""
        task_tickets = await self.tickets.find_by_parent(ticket_id)
        completed = 0
        for tt in task_tickets:
            runs = await self.comments.phase_runs_for(tt.id)
            if any(r.phase_result == "success" for r in runs):
                completed += 1
        return completed

    async def _write_phase_run_comment(self, task_id: str, phase, result) -> None:
        from jig.ticket import Comment
        await self.comments.post(Comment(
            ticket_id=task_id,
            author="orchestrator",
            content=f"phase {phase.name}: {result.status}",
            kind="phase_run",
            phase_result=result.status if result.status in {
                "success", "failed", "blocked", "needs_info"
            } else "failed",
            phase_branch=f"jig/{task_id}",
        ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator_per_ticket.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator_per_ticket.py
git commit -m "feat(orchestrator): per-ticket phase walk (happy path) with phase_run comments"
```

### Task 5.4: Wire `_spawn_qa_responder` to real `run_agent`

**Files:**
- Modify: `jig/orchestrator.py`
- Modify: `tests/test_orchestrator_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_spawn_qa_responder_calls_run_agent(tmp_path: Path, monkeypatch) -> None:
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path), language="python", package_manager="uv"))
    (tmp_path / ".jig" / "agent_types").mkdir(parents=True)
    from jig.persistence import save_agent_type
    from jig.models import AgentTypeConfig
    save_agent_type(tmp_path, AgentTypeConfig(role="qa", phase_prompt="be qa"))

    orch = Orchestrator(project_path=tmp_path)
    calls: list[str] = []

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    async def fake_run_agent(ctx):
        calls.append(ctx.role)
        return RunAgentResult(status="success", final_text="ok")
    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    async def fake_ensure(ticket):
        return tmp_path
    orch._ensure_worktree = fake_ensure  # type: ignore

    await orch.startup()
    try:
        from jig.ticket import Ticket
        tid = await orch.tickets.create(Ticket(
            type=TicketType.QUESTION, title="q", created_by="dev", assignee="qa",
        ))
        from jig.store import Message, MessageType
        fake_msg = Message(
            sender="dev", to="qa", type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "ticket_created", "ticket_id": tid},
            topic=f"tickets.{tid}",
        )
        await orch._spawn_qa_responder(tid, "qa", fake_msg)
        # Give it a moment
        for _ in range(20):
            await asyncio.sleep(0.05)
            if calls:
                break
        assert calls == ["qa"]
    finally:
        await orch.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator_dispatch.py::test_spawn_qa_responder_calls_run_agent -v`
Expected: FAIL, `_spawn_qa_responder` is a stub.

- [ ] **Step 3: Replace `_spawn_qa_responder` with real implementation**

In `jig/orchestrator.py`:

```python
    async def _spawn_qa_responder(
        self, ticket_id: str, role: str, initial_event
    ) -> None:
        from jig.agent import run_agent
        from jig.persistence import load_agent_type
        from jig.runtime import AgentSpawnContext, SpawnReason

        ticket = await self.tickets.get(ticket_id)
        if ticket is None:
            return
        parent = None
        if ticket.parent_id:
            parent = await self.tickets.get(ticket.parent_id)
        role_cfg = load_agent_type(self._project_path, role)
        worktree = await self._ensure_worktree(parent or ticket)

        ctx = AgentSpawnContext(
            role=role,
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.QA_RESPONDER,
            ticket=ticket,
            parent=parent,
            worktree_path=worktree,
            project=self._project,
            tickets=self.tickets,
            comments=self.comments,
            memory=self.memory,
            bus=self.bus,
            initial_bus_message=initial_event.payload if initial_event else None,
        )
        task = asyncio.create_task(run_agent(ctx))
        self._live_subscribers[(ticket_id, role)] = task
        task.add_done_callback(
            lambda _: self._live_subscribers.pop((ticket_id, role), None)
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator_dispatch.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator_dispatch.py
git commit -m "feat(orchestrator): spawn_qa_responder calls run_agent"
```

---

# Phase 6 — Worktree rewrite (per-ticket, not per-phase)

### Task 6.1: Rewrite `create_worktree` and `remove_worktree` to take `ticket_id`

**Files:**
- Modify: `jig/worktree.py`
- Test: `tests/test_worktree_per_ticket.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worktree_per_ticket.py
import subprocess
from pathlib import Path

import pytest

from jig.worktree import create_worktree, remove_worktree


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=path, check=True)


@pytest.mark.asyncio
async def test_create_worktree_per_ticket(tmp_path: Path) -> None:
    _init_git(tmp_path)
    wt = await create_worktree(
        project_path=tmp_path, ticket_id="T-42", base_branch="main",
    )
    assert wt == tmp_path / ".jig" / "worktrees" / "T-42"
    assert wt.is_dir()
    # Branch was created
    result = subprocess.run(
        ["git", "branch", "--list", "jig/T-42"],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    assert "jig/T-42" in result.stdout


@pytest.mark.asyncio
async def test_remove_worktree_per_ticket(tmp_path: Path) -> None:
    _init_git(tmp_path)
    wt = await create_worktree(
        project_path=tmp_path, ticket_id="T-42", base_branch="main",
    )
    assert wt.is_dir()
    await remove_worktree(project_path=tmp_path, ticket_id="T-42")
    assert not wt.is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_worktree_per_ticket.py -v`
Expected: FAIL, `create_worktree` takes a `phase` argument.

- [ ] **Step 3: Rewrite the signatures**

Replace `create_worktree` and `remove_worktree` in `jig/worktree.py`:

```python
async def create_worktree(
    project_path: Path,
    ticket_id: str,
    base_branch: str,
) -> Path:
    worktree_path = project_path / ".jig" / "worktrees" / ticket_id
    branch_name = f"jig/{ticket_id}"

    try:
        await _run_git(project_path, "rev-parse", "HEAD")
    except RuntimeError:
        await _run_git(
            project_path,
            "commit", "--allow-empty", "-m", "chore: initialize repository",
        )

    await _run_git(
        project_path,
        "worktree", "add", "-b", branch_name,
        str(worktree_path), base_branch,
    )
    return worktree_path


async def remove_worktree(project_path: Path, ticket_id: str) -> None:
    worktree_path = project_path / ".jig" / "worktrees" / ticket_id
    branch_name = f"jig/{ticket_id}"
    await _run_git(project_path, "worktree", "remove", str(worktree_path), "--force")
    try:
        await _run_git(project_path, "branch", "-D", branch_name)
    except RuntimeError:
        pass
```

Also rename `merge_issue` → `merge_ticket` and change its `issue_id` param to `ticket_id`; drop the `final_phase` param. Source branch becomes `jig/{ticket_id}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_worktree_per_ticket.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jig/worktree.py tests/test_worktree_per_ticket.py
git commit -m "feat(worktree): switch to per-ticket worktrees and branches"
```

---

# Phase 7 — TUI integration (Python side)

The TypeScript TUI itself lives in `tui/src/`. This phase only lands the Python-side additions needed for the TUI to function: bidirectional websocket commands and ticket-centric events.

### Task 7.1: Bidirectional websocket commands

**Files:**
- Modify: `jig/ws_server.py`
- Test: `tests/test_ws_server.py` (add new tests; keep old)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ws_server.py — add this test
import json

import pytest
import websockets

from jig.events import EventEmitter
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ws_server import WebSocketServer


@pytest.mark.asyncio
async def test_tui_can_create_ticket_via_ws(tmp_path) -> None:
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()

    emitter = EventEmitter()
    server = WebSocketServer(emitter=emitter, host="127.0.0.1", port=0, orchestrator=orch)
    await server.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{server.port}") as ws:
            await ws.send(json.dumps({
                "command": "create_ticket",
                "args": {
                    "type": "feature",
                    "title": "from-tui",
                    "description": "",
                },
            }))
            # Wait for ack reply
            reply = json.loads(await ws.recv())
            assert reply["ok"] is True
            assert "ticket_id" in reply

        tickets = await orch.tickets.list_all()
        assert any(t.title == "from-tui" for t in tickets)
    finally:
        await server.stop()
        await orch.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ws_server.py::test_tui_can_create_ticket_via_ws -v`
Expected: FAIL, `WebSocketServer.__init__` doesn't accept `orchestrator`.

- [ ] **Step 3: Update `WebSocketServer`**

Modify `jig/ws_server.py`:

```python
import json

from jig.ticket_mcp import (
    handle_create_ticket,
    handle_comment_on_ticket,
    handle_update_ticket,
)


class WebSocketServer:
    def __init__(
        self,
        emitter: EventEmitter,
        host: str = "127.0.0.1",
        port: int = 9100,
        orchestrator=None,
    ) -> None:
        self._emitter = emitter
        self._host = host
        self._port = port
        self._orch = orchestrator
        # ... existing init ...
        self._server = None
        self._relay_task = None
        self._clients = set()
        self._queue = emitter.subscribe()
        self._history: list[str] = []

    async def _handle_client(self, websocket) -> None:
        for message in self._history:
            try:
                await websocket.send(message)
            except websockets.ConnectionClosed:
                return
        self._clients.add(websocket)
        try:
            async for raw in websocket:
                await self._handle_incoming(websocket, raw)
        finally:
            self._clients.discard(websocket)

    async def _handle_incoming(self, websocket, raw: str) -> None:
        if self._orch is None:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send(json.dumps({"ok": False, "error": "bad json"}))
            return
        command = payload.get("command")
        args = payload.get("args", {})
        try:
            if command == "create_ticket":
                tid = await handle_create_ticket(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    bus=self._orch.bus,
                    sender="user",
                    args=args,
                )
                await websocket.send(json.dumps({"ok": True, "ticket_id": tid}))
            elif command == "comment_on_ticket":
                cid = await handle_comment_on_ticket(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    bus=self._orch.bus,
                    sender="user",
                    sender_cfg=None,
                    args=args,
                )
                await websocket.send(json.dumps({"ok": True, "comment_id": cid}))
            elif command == "update_ticket":
                updated = await handle_update_ticket(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    bus=self._orch.bus,
                    sender="user",
                    args=args,
                )
                await websocket.send(json.dumps({"ok": True, "status": updated.status.value}))
            else:
                await websocket.send(json.dumps({"ok": False, "error": f"unknown command {command}"}))
        except Exception as exc:
            await websocket.send(json.dumps({"ok": False, "error": str(exc)}))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ws_server.py -v`
Expected: PASS (including the new test)

- [ ] **Step 5: Commit**

```bash
git add jig/ws_server.py tests/test_ws_server.py
git commit -m "feat(ws): accept create_ticket/comment_on_ticket/update_ticket commands from TUI"
```

### Task 7.2: Bridge bus → events emitter for ticket-centric events

**Files:**
- Modify: `jig/orchestrator.py`
- Modify: `tests/test_orchestrator_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_orchestrator_emits_ticket_events_to_emitter(tmp_path: Path) -> None:
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    from jig.events import EventEmitter
    emitter = EventEmitter()
    orch = Orchestrator(project_path=tmp_path, emitter=emitter)
    await orch.startup()
    try:
        queue = emitter.subscribe()
        from jig.ticket import Ticket
        await orch.tickets.create(Ticket(type=TicketType.FEATURE, title="f", created_by="user"))
        # Publish a ticket_created event on the bus
        from jig.store import Message, MessageType
        await orch.bus.publish(Message(
            sender="user", to="orchestrator", type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "ticket_created"},
            topic="orchestrator",
        ))
        await asyncio.sleep(0.1)
        # Drain emitter
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        kinds = [e.type for e in events]
        assert "ticket_created" in kinds
    finally:
        await orch.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator_dispatch.py::test_orchestrator_emits_ticket_events_to_emitter -v`
Expected: FAIL, `Orchestrator` doesn't accept `emitter`.

- [ ] **Step 3: Implement**

In `jig/orchestrator.py`, accept an optional `emitter: EventEmitter`. In `_run_dispatch_loop`, forward each bus message to `self._emitter.emit(JigEvent(type=payload["kind"], data=payload))` if `self._emitter` is set.

```python
    def __init__(self, project_path: Path, emitter=None) -> None:
        # ... existing init ...
        self._emitter = emitter

    async def _run_dispatch_loop(self) -> None:
        queue: asyncio.Queue = asyncio.Queue()

        async def _forward(message) -> None:
            await queue.put(message)

        await self.bus.add_websocket_listener(_forward)

        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            # Mirror to emitter for TUI consumption
            if self._emitter is not None:
                from jig.events import JigEvent
                payload = msg.payload or {}
                kind = payload.get("kind", "event")
                await self._emitter.emit(JigEvent(type=kind, data=payload))

            target = self._resolve_target(msg)
            if target is None:
                continue
            ticket_id, role = target
            if role == "user":
                continue
            if (ticket_id, role) in self._live_subscribers:
                continue
            await self._spawn_qa_responder(ticket_id, role, msg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator_dispatch.py::test_orchestrator_emits_ticket_events_to_emitter -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator_dispatch.py
git commit -m "feat(orchestrator): forward bus events to EventEmitter for TUI"
```

---

# Phase 8 — Delete the old world

### Task 8.1: Delete `pool.py`, `bus_monitor.py`, `mcp_tools.py` and their tests

**Files:**
- Delete: `jig/pool.py`
- Delete: `jig/bus_monitor.py`
- Delete: `jig/mcp_tools.py`
- Delete: `tests/test_pool.py`
- Delete: `tests/test_bus_monitor.py`
- Delete: `tests/test_mcp_tools.py`

- [ ] **Step 1: Confirm nothing imports the deletions**

Run: `uv run grep -rn "from jig.pool" jig/ tests/`
Run: `uv run grep -rn "from jig.bus_monitor" jig/ tests/`
Run: `uv run grep -rn "from jig.mcp_tools" jig/ tests/`
Each should produce zero matches. If anything remains, fix it before deletion (usually by updating an import in `jig/orchestrator.py` or `jig/mcp_server.py`).

- [ ] **Step 2: Delete the files**

```bash
rm jig/pool.py jig/bus_monitor.py jig/mcp_tools.py
rm tests/test_pool.py tests/test_bus_monitor.py tests/test_mcp_tools.py
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -x`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: delete pool, bus_monitor, mcp_tools modules and tests"
```

### Task 8.2: Delete obsolete model classes from `jig/models.py`

**Files:**
- Modify: `jig/models.py`

- [ ] **Step 1: Remove the dead classes**

Delete from `jig/models.py`:
- `IssueStatus`
- `CompletionState`
- `Issue`
- `Task`
- `PhaseHistoryEntry`
- `MessageDirection`
- `AgentMessage`
- `AgentStatus`
- `AgentInstance`
- `CompletionStatus`
- `CompletionReport`

Keep: `ProjectConfig`, `MergeStrategy`, `ProjectContext`, `AgentTypeConfig`, `PhaseConfig`, `WorkflowConfig`.

- [ ] **Step 2: Remove dead helpers in `jig/persistence.py`**

Delete: `save_issue`, `load_issue`, `list_issues`, `save_task`, `load_task`, `save_agent_instance`, `load_agent_instance`, `list_agent_instances`, `load_skill` (replaced by `skill_loader`).

Keep: `save_agent_type`, `load_agent_type`, `list_agent_types`, `save_default_agent_types`, `save_workflow`, `load_workflow`, `save_default_workflow`.

- [ ] **Step 3: Run grep for orphan imports**

Run: `uv run grep -rn "AgentInstance\|IssueStatus\|CompletionReport\|CompletionState\|PhaseHistoryEntry\|AgentMessage\|MessageDirection\|load_issue\|save_issue\|save_task\|load_task\|load_agent_instance" jig/ tests/`

Expected: zero matches. Fix anything that remains.

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest -x`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jig/models.py jig/persistence.py
git commit -m "chore: delete obsolete models and persistence helpers"
```

### Task 8.3: Delete `jig/defaults/skills/`

**Files:**
- Delete: `jig/defaults/skills/*.md`

- [ ] **Step 1: Confirm nothing references `jig/defaults/skills/`**

Run: `uv run grep -rn "defaults/skills" jig/ tests/`
Run: `uv run grep -rn "load_skill\\b" jig/ tests/`
Expected: zero matches.

- [ ] **Step 2: Delete the directory**

```bash
rm -r jig/defaults/skills
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest -x`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: delete legacy jig/defaults/skills directory"
```

---

# Phase 9 — Final sweep: cli, tests, and leftover references

### Task 9.1: Update `jig init` for the new `.jig/` layout

**Files:**
- Modify: `jig/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — replace or augment test_init_creates_jig_dir
def test_init_creates_project_json(tmp_path: Path, runner) -> None:
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True)
    result = runner.invoke(app, ["init", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / ".jig" / "project.json").is_file()
    assert not (tmp_path / ".jig" / "issues").exists()
    assert (tmp_path / ".jig" / "worktrees").is_dir()
    assert (tmp_path / ".jig" / "agent_types").is_dir()
    assert (tmp_path / ".jig" / "workflows").is_dir()
    assert (tmp_path / ".jig" / "store").is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_init_creates_project_json -v`
Expected: FAIL, `init` still creates the old layout.

- [ ] **Step 3: Update `init_project` and the CLI**

In `jig/persistence.py`, replace `init_project`:

```python
def init_project(project_path: Path, default_branch: str = "main") -> None:
    if not (project_path / ".git").is_dir():
        raise ValueError(f"{project_path} is not a git repository")
    jig_dir = _jig_dir(project_path)
    if jig_dir.exists():
        raise FileExistsError(f"{jig_dir} already exists")
    jig_dir.mkdir()
    for subdir in ("agent_types", "workflows", "worktrees", "store"):
        (jig_dir / subdir).mkdir()
```

In `jig/cli.py`, after init, also call `save_project(...)` with the prompted values, constructing a `Project(...)` instance instead of `ProjectContext(...)`. The interactive prompt gathering (`_prompt_project_context`) continues to collect the same fields; only the persistence target changes.

Also: `jig/cli.py`'s `start` command needs to construct `Orchestrator(project_path=...)` (no bus injection), call `startup()`, and run the websocket server alongside.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jig/cli.py jig/persistence.py tests/test_cli.py
git commit -m "feat(cli): init writes .jig/project.json with new layout"
```

### Task 9.2: Rewrite `tests/test_orchestrator.py`

**Files:**
- Rewrite: `tests/test_orchestrator.py`

- [ ] **Step 1: Replace the file with a thin smoke test**

The heavy lifting is covered by `test_orchestrator_dispatch.py` and `test_orchestrator_per_ticket.py`. `test_orchestrator.py` becomes a smoke test that wires up a real orchestrator + one mock run_agent and verifies an end-to-end feature ticket walks to resolved. If the file overlaps significantly with the per-ticket test, reduce it to a single integration test or delete it.

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest`
Expected: PASS, no tests skipped unexpectedly.

- [ ] **Step 3: Commit**

```bash
git add tests/test_orchestrator.py
git commit -m "test(orchestrator): consolidate into smoke test over new runtime"
```

### Task 9.3: Rewrite `tests/test_persistence.py` and `tests/test_models.py`

**Files:**
- Rewrite: `tests/test_persistence.py`
- Rewrite: `tests/test_models.py`

- [ ] **Step 1: Trim dead coverage**

Delete tests that reference `Issue`, `Task`, `AgentInstance`, `PhaseHistoryEntry`, `load_task`, `save_issue`, etc. Keep agent-type and workflow persistence tests. Keep `AgentTypeConfig` tests (new fields already covered in Phase 0.3).

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_persistence.py tests/test_models.py
git commit -m "test: trim persistence and model tests to new surface"
```

### Task 9.4: Final grep sweep

- [ ] **Step 1: Ensure no production references to dead names**

Run each of these and fix anything that appears in `jig/` (tests are allowed to mention them in legacy filenames only if no such files remain):

```
uv run grep -rn "AgentInstance\|AgentPool\|BusMonitor\|Issue\b\|Task\b\|PhaseHistoryEntry\|AgentMessage\|send_message\|check_messages" jig/
```

All production references should be gone. If a grep hit appears, fix it.

- [ ] **Step 2: Run the full test suite and ruff**

Run: `uv run pytest && uv run ruff check jig/ tests/`
Expected: all PASS, ruff clean.

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "chore: final cleanup of dead references"
```

---

## Success checks

After all phases land, verify the spec's success criteria:

- [ ] A FEATURE ticket created via `handle_create_ticket` walks spec → dev → qa with a stubbed `run_agent` and reaches `RESOLVED`. (Covered by `test_orchestrator_per_ticket.py`.)
- [ ] A QUESTION ticket assigned to a role that has no live subscriber causes `_spawn_qa_responder` to be called. (Covered by `test_orchestrator_dispatch.py`.)
- [ ] A QUESTION ticket with `assignee="user"` does NOT cause dispatch to spawn an agent. (Covered by `test_orchestrator_dispatch.py::test_dispatch_loop_skips_user_role`.)
- [ ] Killing the orchestrator mid-run and restarting it resumes in-progress tickets. (Covered by `test_orchestrator_dispatch.py::test_orchestrator_resumes_in_progress_tickets`.)
- [ ] `build_initial_prompt` injects content in the spec's documented order. (Covered by `test_prompt_builder.py::test_injection_order`.)
- [ ] `load_all_skills()` returns frontmatter-bearing skills and `match_skills` filters correctly. (Covered by `test_skill_loader.py`.)
- [ ] Full grep for `AgentInstance`, `AgentPool`, `Issue`, `Task`, `PhaseHistoryEntry`, `send_message`, `check_messages` in `jig/` returns empty.

---
