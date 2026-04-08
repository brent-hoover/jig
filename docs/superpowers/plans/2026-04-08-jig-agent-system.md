# Jig Agent System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable spawning a single Claude Code agent in an isolated git worktree with custom MCP tools (publish_message, report_completion, request_context), configured by agent type YAML files.

**Architecture:** Agent types are YAML configs loaded from `.jig/agent_types/`. Git worktrees provide workspace isolation per agent. A factory function creates a Jig MCP server with tools that close over the message bus and issue state. An agent runner wires up `claude_agent_sdk` with the MCP server, worktree, and agent type config.

**Tech Stack:** Python 3.12+, claude-agent-sdk, Pydantic, PyYAML, asyncio, pytest

**Depends on:** Plan 1 (Foundation) — models, persistence, message bus, CLI

---

## File Structure

```
jig/
├── models.py            # (modify) Add AgentTypeConfig model
├── persistence.py       # (modify) Add agent type load/list/save + task persistence
├── worktree.py          # (create) Git worktree create/commit/remove
├── mcp_tools.py         # (create) Jig MCP tool handler functions + server factory
├── agent.py             # (create) Agent runner (spawn via SDK, collect result)
├── cli.py               # (modify) Update jig init to create default agent types
tests/
├── test_models.py       # (modify) Add AgentTypeConfig tests
├── test_persistence.py  # (modify) Add agent type + task persistence tests
├── test_worktree.py     # (create) Worktree integration tests (real git repos)
├── test_mcp_tools.py    # (create) MCP tool handler unit tests
├── test_agent.py        # (create) Agent runner tests (mocked SDK)
```

---

### Task 1: Add claude-agent-sdk Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add claude-agent-sdk to dependencies**

In `pyproject.toml`, update the dependencies list:

```toml
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "click>=8.0",
    "claude-agent-sdk>=0.1.0",
]
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync`
Expected: claude-agent-sdk installed successfully

- [ ] **Step 3: Verify import works**

Run: `uv run python -c "from claude_agent_sdk import query, ClaudeAgentOptions; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat: add claude-agent-sdk dependency"
```

---

### Task 2: AgentTypeConfig Model

**Files:**
- Modify: `jig/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write failing tests for AgentTypeConfig**

Append to `tests/test_models.py`:

```python
from jig.models import AgentTypeConfig


class TestAgentTypeConfig:
    def test_defaults(self):
        config = AgentTypeConfig(
            name="dev",
            system_prompt="You are a dev agent.",
        )
        assert config.name == "dev"
        assert config.system_prompt == "You are a dev agent."
        assert config.allowed_tools == []
        assert config.denied_tools == []
        assert config.default_context == []

    def test_full_config(self):
        config = AgentTypeConfig(
            name="test",
            system_prompt="You are a test agent.",
            allowed_tools=["Read", "Bash", "Grep"],
            denied_tools=["Write"],
            default_context=["issue://design", "**/*_test.py"],
        )
        assert config.allowed_tools == ["Read", "Bash", "Grep"]
        assert config.denied_tools == ["Write"]
        assert config.default_context == ["issue://design", "**/*_test.py"]

    def test_serialization_roundtrip(self):
        config = AgentTypeConfig(
            name="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )
        data = config.model_dump()
        restored = AgentTypeConfig.model_validate(data)
        assert restored.name == config.name
        assert restored.allowed_tools == config.allowed_tools
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::TestAgentTypeConfig -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement AgentTypeConfig**

Append to `jig/models.py`:

```python
class AgentTypeConfig(BaseModel):
    name: str
    system_prompt: str
    allowed_tools: list[str] = []
    denied_tools: list[str] = []
    default_context: list[str] = []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/models.py tests/test_models.py
git commit -m "feat: add AgentTypeConfig model"
```

---

### Task 3: Agent Type Persistence

**Files:**
- Modify: `jig/persistence.py`
- Modify: `tests/test_persistence.py`

- [ ] **Step 1: Write failing tests for agent type persistence**

Append to `tests/test_persistence.py`:

```python
from jig.models import AgentTypeConfig
from jig.persistence import save_agent_type, load_agent_type, list_agent_types


class TestAgentTypePersistence:
    def test_save_and_load(self, tmp_jig_project: Path):
        config = AgentTypeConfig(
            name="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )
        save_agent_type(tmp_jig_project, config)
        loaded = load_agent_type(tmp_jig_project, "dev")
        assert loaded.name == "dev"
        assert loaded.system_prompt == "You are a dev agent."
        assert loaded.allowed_tools == ["Read", "Edit"]

    def test_saves_to_correct_path(self, tmp_jig_project: Path):
        config = AgentTypeConfig(name="test", system_prompt="Test agent.")
        save_agent_type(tmp_jig_project, config)
        yaml_path = tmp_jig_project / ".jig" / "agent_types" / "test.yaml"
        assert yaml_path.is_file()

    def test_list_empty(self, tmp_jig_project: Path):
        types = list_agent_types(tmp_jig_project)
        assert types == []

    def test_list_multiple(self, tmp_jig_project: Path):
        save_agent_type(tmp_jig_project, AgentTypeConfig(name="dev", system_prompt="Dev."))
        save_agent_type(tmp_jig_project, AgentTypeConfig(name="test", system_prompt="Test."))
        types = list_agent_types(tmp_jig_project)
        names = {t.name for t in types}
        assert names == {"dev", "test"}

    def test_load_nonexistent_raises(self, tmp_jig_project: Path):
        with pytest.raises(FileNotFoundError):
            load_agent_type(tmp_jig_project, "nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_persistence.py::TestAgentTypePersistence -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement agent type persistence**

Add `AgentTypeConfig` to the import from `jig.models` at the top of `jig/persistence.py`:

```python
from jig.models import ProjectConfig, Issue, Message, AgentTypeConfig
```

Append these functions to `jig/persistence.py`:

```python
def save_agent_type(project_path: Path, config: AgentTypeConfig) -> None:
    """Save an agent type config to .jig/agent_types/<name>.yaml."""
    type_path = _jig_dir(project_path) / "agent_types" / f"{config.name}.yaml"
    type_path.write_text(
        yaml.dump(config.model_dump(), default_flow_style=False)
    )


def load_agent_type(project_path: Path, name: str) -> AgentTypeConfig:
    """Load an agent type config from .jig/agent_types/<name>.yaml."""
    type_path = _jig_dir(project_path) / "agent_types" / f"{name}.yaml"
    if not type_path.is_file():
        raise FileNotFoundError(f"Agent type '{name}' not found")
    data = yaml.safe_load(type_path.read_text())
    return AgentTypeConfig.model_validate(data)


def list_agent_types(project_path: Path) -> list[AgentTypeConfig]:
    """List all agent type configs in .jig/agent_types/."""
    types_dir = _jig_dir(project_path) / "agent_types"
    configs = []
    for yaml_file in sorted(types_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        configs.append(AgentTypeConfig.model_validate(data))
    return configs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_persistence.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/persistence.py tests/test_persistence.py
git commit -m "feat: add agent type persistence (save, load, list)"
```

---

### Task 4: Default Agent Type Configs and Updated Init

**Files:**
- Modify: `jig/persistence.py`
- Modify: `jig/cli.py`
- Modify: `tests/test_persistence.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test for save_default_agent_types**

Append to `tests/test_persistence.py`:

```python
from jig.persistence import save_default_agent_types


class TestDefaultAgentTypes:
    def test_creates_four_types(self, tmp_jig_project: Path):
        save_default_agent_types(tmp_jig_project)
        types = list_agent_types(tmp_jig_project)
        names = {t.name for t in types}
        assert names == {"spec", "test", "dev", "review"}

    def test_each_has_system_prompt(self, tmp_jig_project: Path):
        save_default_agent_types(tmp_jig_project)
        for name in ("spec", "test", "dev", "review"):
            config = load_agent_type(tmp_jig_project, name)
            assert len(config.system_prompt) > 0

    def test_each_has_allowed_tools(self, tmp_jig_project: Path):
        save_default_agent_types(tmp_jig_project)
        for name in ("spec", "test", "dev", "review"):
            config = load_agent_type(tmp_jig_project, name)
            assert len(config.allowed_tools) > 0

    def test_each_has_default_context(self, tmp_jig_project: Path):
        save_default_agent_types(tmp_jig_project)
        for name in ("spec", "test", "dev", "review"):
            config = load_agent_type(tmp_jig_project, name)
            assert len(config.default_context) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_persistence.py::TestDefaultAgentTypes -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement save_default_agent_types**

Append to `jig/persistence.py`:

```python
def save_default_agent_types(project_path: Path) -> None:
    """Create the default v1 agent type configs."""
    defaults = [
        AgentTypeConfig(
            name="spec",
            system_prompt=(
                "You are a specification agent. Your job is to draft a design document "
                "from the issue description. Analyze requirements, identify components, "
                "and produce a clear, actionable design doc in markdown format. "
                "Use the report_completion tool when finished."
            ),
            allowed_tools=["Read", "Glob", "Grep"],
            default_context=["issue://description"],
        ),
        AgentTypeConfig(
            name="test",
            system_prompt=(
                "You are a test agent. Your job is to write tests based on the design doc "
                "and implementation plan. Write comprehensive tests that cover the specified "
                "behavior and edge cases. Use the report_completion tool when finished."
            ),
            allowed_tools=["Read", "Write", "Glob", "Grep", "Bash"],
            default_context=["issue://design", "issue://plan"],
        ),
        AgentTypeConfig(
            name="dev",
            system_prompt=(
                "You are a development agent. Your job is to implement code changes based "
                "on the design doc and implementation plan. Write clean, well-structured code "
                "that passes the existing tests. Use the report_completion tool when finished."
            ),
            allowed_tools=["Read", "Edit", "Write", "Glob", "Grep", "Bash"],
            default_context=["issue://design", "issue://plan"],
        ),
        AgentTypeConfig(
            name="review",
            system_prompt=(
                "You are a review agent. Your job is to review the implementation for "
                "correctness, quality, and adherence to the design doc. Run tests and linters. "
                "Report issues found. Use the report_completion tool when finished."
            ),
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
            default_context=["issue://design", "issue://plan"],
        ),
    ]
    for config in defaults:
        save_agent_type(project_path, config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_persistence.py -v`
Expected: all passed

- [ ] **Step 5: Write failing test for updated jig init**

Append to `tests/test_cli.py`:

```python
from jig.persistence import list_agent_types


class TestInitCreatesAgentTypes:
    def test_init_creates_default_agent_types(self, runner: CliRunner, tmp_project: Path):
        result = runner.invoke(cli, ["init", "--path", str(tmp_project)])
        assert result.exit_code == 0
        types = list_agent_types(tmp_project)
        names = {t.name for t in types}
        assert names == {"spec", "test", "dev", "review"}
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::TestInitCreatesAgentTypes -v`
Expected: FAIL (init doesn't create agent types yet)

- [ ] **Step 7: Update jig init to create default agent types**

In `jig/cli.py`, update the import:

```python
from jig.persistence import init_project, list_issues, load_project, save_default_agent_types
```

In the `init` command function, add `save_default_agent_types(path)` after the `init_project` call:

```python
        init_project(path, default_branch=branch)
        save_default_agent_types(path)
        click.echo(f"Initialized Jig in {path / '.jig'}")
```

- [ ] **Step 8: Run all CLI tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all passed

- [ ] **Step 9: Commit**

```bash
git add jig/persistence.py jig/cli.py tests/test_persistence.py tests/test_cli.py
git commit -m "feat: add default agent type configs and update jig init"
```

---

### Task 5: Task Persistence

**Files:**
- Modify: `jig/persistence.py`
- Modify: `tests/test_persistence.py`

- [ ] **Step 1: Write failing tests for task persistence**

Append to `tests/test_persistence.py`:

```python
from jig.models import Task, CompletionState
from jig.persistence import save_task, load_task


class TestTaskPersistence:
    def test_save_and_load(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        task = Task(
            id="task-1",
            description="Implement feature",
            acceptance_criteria="Tests pass",
            agent_type="dev",
        )
        save_task(tmp_jig_project, "issue-1", task)
        loaded = load_task(tmp_jig_project, "issue-1", "task-1")
        assert loaded.id == "task-1"
        assert loaded.description == "Implement feature"
        assert loaded.agent_type == "dev"

    def test_saves_to_correct_path(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        task = Task(id="task-1", description="Do thing", acceptance_criteria="Done", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        task_path = tmp_jig_project / ".jig" / "issues" / "issue-1" / "tasks" / "task-1.yaml"
        assert task_path.is_file()

    def test_update_with_completion(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        task = Task(id="task-1", description="Do thing", acceptance_criteria="Done", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        task.completion_state = CompletionState.SUCCESS
        task.completion_reason = "All tests pass"
        save_task(tmp_jig_project, "issue-1", task)
        loaded = load_task(tmp_jig_project, "issue-1", "task-1")
        assert loaded.completion_state == CompletionState.SUCCESS
        assert loaded.completion_reason == "All tests pass"

    def test_load_nonexistent_raises(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        with pytest.raises(FileNotFoundError):
            load_task(tmp_jig_project, "issue-1", "nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_persistence.py::TestTaskPersistence -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement task persistence**

Add `Task` to the models import at the top of `jig/persistence.py`:

```python
from jig.models import ProjectConfig, Issue, Message, AgentTypeConfig, Task
```

Append to `jig/persistence.py`:

```python
def save_task(project_path: Path, issue_id: str, task: Task) -> None:
    """Save a task to .jig/issues/<issue_id>/tasks/<task_id>.yaml."""
    tasks_dir = _jig_dir(project_path) / "issues" / issue_id / "tasks"
    tasks_dir.mkdir(exist_ok=True)
    task_path = tasks_dir / f"{task.id}.yaml"
    task_path.write_text(
        yaml.dump(task.model_dump(mode="json"), default_flow_style=False)
    )


def load_task(project_path: Path, issue_id: str, task_id: str) -> Task:
    """Load a task from .jig/issues/<issue_id>/tasks/<task_id>.yaml."""
    task_path = _jig_dir(project_path) / "issues" / issue_id / "tasks" / f"{task_id}.yaml"
    if not task_path.is_file():
        raise FileNotFoundError(f"Task {task_id} not found in issue {issue_id}")
    data = yaml.safe_load(task_path.read_text())
    return Task.model_validate(data)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_persistence.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/persistence.py tests/test_persistence.py
git commit -m "feat: add task persistence (save, load)"
```

---

### Task 6: Worktree Management

**Files:**
- Create: `jig/worktree.py`
- Create: `tests/test_worktree.py`

- [ ] **Step 1: Write failing tests for worktree management**

`tests/test_worktree.py`:

```python
import subprocess
from pathlib import Path

import pytest

from jig.worktree import create_worktree, remove_worktree, commit_worktree


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a real git repo with an initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    (repo / ".jig" / "worktrees" / "issue-1").mkdir(parents=True)
    return repo


class TestCreateWorktree:
    async def test_creates_worktree_directory(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "spec", "main")
        assert wt_path.is_dir()
        assert (wt_path / "README.md").is_file()

    async def test_worktree_is_on_new_branch(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "spec", "main")
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt_path, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "jig/issue-1/spec"

    async def test_worktree_path(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "spec", "main")
        expected = git_repo / ".jig" / "worktrees" / "issue-1" / "spec"
        assert wt_path == expected


class TestCommitWorktree:
    async def test_commits_changes(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "spec", "main")
        (wt_path / "design.md").write_text("# Design\n")
        sha = await commit_worktree(wt_path, "spec: draft design doc")
        assert sha  # non-empty string
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=wt_path, capture_output=True, text=True, check=True,
        )
        assert "spec: draft design doc" in result.stdout

    async def test_returns_none_if_no_changes(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "spec", "main")
        sha = await commit_worktree(wt_path, "nothing to commit")
        assert sha is None


class TestRemoveWorktree:
    async def test_removes_worktree(self, git_repo: Path):
        wt_path = await create_worktree(git_repo, "issue-1", "spec", "main")
        assert wt_path.is_dir()
        await remove_worktree(git_repo, "issue-1", "spec")
        assert not wt_path.is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_worktree.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement worktree management**

`jig/worktree.py`:

```python
"""Git worktree management for agent isolation."""

import asyncio
from pathlib import Path


async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def create_worktree(
    project_path: Path,
    issue_id: str,
    phase: str,
    base_branch: str,
) -> Path:
    """Create a git worktree for an agent.

    Returns the path to the worktree directory.
    """
    worktree_path = project_path / ".jig" / "worktrees" / issue_id / phase
    branch_name = f"jig/{issue_id}/{phase}"
    await _run_git(
        project_path,
        "worktree", "add", "-b", branch_name,
        str(worktree_path), base_branch,
    )
    return worktree_path


async def commit_worktree(worktree_path: Path, message: str) -> str | None:
    """Commit all changes in a worktree.

    Returns the commit SHA, or None if there were no changes.
    """
    await _run_git(worktree_path, "add", "-A")

    # Check if there's anything staged
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--cached", "--quiet",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode == 0:
        return None  # Nothing staged

    await _run_git(worktree_path, "commit", "-m", message)
    sha = await _run_git(worktree_path, "rev-parse", "HEAD")
    return sha


async def remove_worktree(
    project_path: Path,
    issue_id: str,
    phase: str,
) -> None:
    """Remove a git worktree and its branch."""
    worktree_path = project_path / ".jig" / "worktrees" / issue_id / phase
    branch_name = f"jig/{issue_id}/{phase}"
    await _run_git(project_path, "worktree", "remove", str(worktree_path), "--force")
    try:
        await _run_git(project_path, "branch", "-D", branch_name)
    except RuntimeError:
        pass  # Branch may already be deleted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_worktree.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add jig/worktree.py tests/test_worktree.py
git commit -m "feat: add git worktree management (create, commit, remove)"
```

---

### Task 7: MCP Tool Handlers

**Files:**
- Create: `jig/mcp_tools.py`
- Create: `tests/test_mcp_tools.py`

The MCP tool handler functions contain the core logic. They are plain async functions testable without the SDK. The server factory (Task 8) wraps them with `@tool` decorators.

- [ ] **Step 1: Write failing tests for handle_publish_message**

`tests/test_mcp_tools.py`:

```python
import json
from pathlib import Path

import pytest

from jig.bus import MessageBus
from jig.models import Issue, Message, MessageType, Task, CompletionState
from jig.mcp_tools import handle_publish_message, handle_report_completion, handle_request_context
from jig.persistence import save_issue, save_task, load_task


class TestHandlePublishMessage:
    @pytest.fixture
    def bus(self, tmp_jig_project: Path) -> MessageBus:
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        return MessageBus(tmp_jig_project)

    async def test_publishes_message(self, bus: MessageBus, tmp_jig_project: Path):
        queue = await bus.subscribe("issue-1", "orchestrator")
        result = await handle_publish_message(
            bus=bus,
            issue_id="issue-1",
            sender="dev-agent",
            args={
                "recipient": "orchestrator",
                "message_type": "status",
                "payload": json.dumps({"info": "working"}),
            },
        )
        assert "sent" in result.lower()
        msg = await queue.get()
        assert msg.sender == "dev-agent"
        assert msg.recipient == "orchestrator"
        assert msg.type == MessageType.STATUS
        assert msg.payload == {"info": "working"}

    async def test_empty_payload(self, bus: MessageBus, tmp_jig_project: Path):
        queue = await bus.subscribe("issue-1", "orchestrator")
        result = await handle_publish_message(
            bus=bus,
            issue_id="issue-1",
            sender="dev-agent",
            args={
                "recipient": "orchestrator",
                "message_type": "status",
            },
        )
        msg = await queue.get()
        assert msg.payload == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandlePublishMessage -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement handle_publish_message**

`jig/mcp_tools.py`:

```python
"""Jig MCP tool handler functions.

These are the core logic functions called by the MCP server tools.
Separated from the server factory for testability.
"""

import json
from pathlib import Path

from jig.bus import MessageBus
from jig.models import Message, MessageType, CompletionState
from jig.persistence import save_task, load_task


async def handle_publish_message(
    bus: MessageBus,
    issue_id: str,
    sender: str,
    args: dict,
) -> str:
    """Publish a message to the bus. Returns confirmation text."""
    payload_str = args.get("payload", "{}")
    try:
        payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
    except json.JSONDecodeError:
        payload = {}

    msg = Message(
        sender=sender,
        recipient=args["recipient"],
        type=MessageType(args["message_type"]),
        payload=payload,
    )
    await bus.publish(issue_id, msg)
    return f"Message sent to {args['recipient']}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandlePublishMessage -v`
Expected: 2 passed

- [ ] **Step 5: Write failing tests for handle_report_completion**

Append to `tests/test_mcp_tools.py`:

```python
class TestHandleReportCompletion:
    def _setup_issue_and_task(self, tmp_jig_project: Path) -> None:
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(
            id="task-1",
            description="Do thing",
            acceptance_criteria="Done",
            agent_type="dev",
        )
        save_task(tmp_jig_project, "issue-1", task)

    async def test_marks_task_success(self, tmp_jig_project: Path):
        self._setup_issue_and_task(tmp_jig_project)
        result = await handle_report_completion(
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            args={"status": "success", "reason": "All tests pass"},
        )
        assert "success" in result.lower()
        task = load_task(tmp_jig_project, "issue-1", "task-1")
        assert task.completion_state == CompletionState.SUCCESS
        assert task.completion_reason == "All tests pass"

    async def test_marks_task_needs_info(self, tmp_jig_project: Path):
        self._setup_issue_and_task(tmp_jig_project)
        result = await handle_report_completion(
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            args={"status": "needs_info", "reason": "Missing API spec"},
        )
        task = load_task(tmp_jig_project, "issue-1", "task-1")
        assert task.completion_state == CompletionState.NEEDS_INFO
        assert task.completion_reason == "Missing API spec"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandleReportCompletion -v`
Expected: FAIL

- [ ] **Step 7: Implement handle_report_completion**

Append to `jig/mcp_tools.py`:

```python
async def handle_report_completion(
    project_path: Path,
    issue_id: str,
    task_id: str,
    args: dict,
) -> str:
    """Update task completion state. Returns confirmation text."""
    task = load_task(project_path, issue_id, task_id)
    task.completion_state = CompletionState(args["status"])
    task.completion_reason = args.get("reason", "")
    save_task(project_path, issue_id, task)
    return f"Completion reported: {args['status']}"
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_tools.py -v`
Expected: 4 passed

- [ ] **Step 9: Write failing tests for handle_request_context**

Append to `tests/test_mcp_tools.py`:

```python
class TestHandleRequestContext:
    async def test_reads_file(self, tmp_jig_project: Path):
        (tmp_jig_project / "src.py").write_text("print('hello')")
        result = await handle_request_context(
            worktree_path=tmp_jig_project,
            args={"path": "src.py"},
        )
        assert "print('hello')" in result

    async def test_file_not_found(self, tmp_jig_project: Path):
        result = await handle_request_context(
            worktree_path=tmp_jig_project,
            args={"path": "nonexistent.py"},
        )
        assert "not found" in result.lower()
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_tools.py::TestHandleRequestContext -v`
Expected: FAIL

- [ ] **Step 11: Implement handle_request_context**

Append to `jig/mcp_tools.py`:

```python
async def handle_request_context(
    worktree_path: Path,
    args: dict,
) -> str:
    """Read a file from the worktree. Returns file contents or error."""
    file_path = worktree_path / args["path"]
    if not file_path.is_file():
        return f"File not found: {args['path']}"
    try:
        return file_path.read_text()
    except Exception as e:
        return f"Error reading {args['path']}: {e}"
```

- [ ] **Step 12: Run all MCP tool tests**

Run: `uv run pytest tests/test_mcp_tools.py -v`
Expected: 6 passed

- [ ] **Step 13: Commit**

```bash
git add jig/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: add MCP tool handler functions"
```

---

### Task 8: MCP Server Factory

**Files:**
- Modify: `jig/mcp_tools.py`
- Modify: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write failing test for create_jig_mcp_server**

Append to `tests/test_mcp_tools.py`:

```python
from jig.mcp_tools import create_jig_mcp_server


class TestCreateJigMcpServer:
    def test_returns_server_config(self, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(id="task-1", description="Do", acceptance_criteria="Done", agent_type="dev")
        save_task(tmp_jig_project, "issue-1", task)
        bus = MessageBus(tmp_jig_project)
        server = create_jig_mcp_server(
            bus=bus,
            project_path=tmp_jig_project,
            issue_id="issue-1",
            task_id="task-1",
            agent_name="dev-agent",
            worktree_path=tmp_jig_project,
        )
        assert server is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_tools.py::TestCreateJigMcpServer -v`
Expected: FAIL

- [ ] **Step 3: Implement create_jig_mcp_server**

Append to `jig/mcp_tools.py`:

```python
from claude_agent_sdk import tool, create_sdk_mcp_server


def create_jig_mcp_server(
    bus: MessageBus,
    project_path: Path,
    issue_id: str,
    task_id: str,
    agent_name: str,
    worktree_path: Path,
):
    """Create a Jig MCP server with tools bound to the current context.

    Returns a server config suitable for ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "publish_message",
        "Send a message to another agent or the orchestrator",
        {"recipient": str, "message_type": str, "payload": str},
    )
    async def publish_message_tool(args):
        text = await handle_publish_message(
            bus=bus,
            issue_id=issue_id,
            sender=agent_name,
            args=args,
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "report_completion",
        "Signal that your task is complete with a status and reason",
        {"status": str, "reason": str},
    )
    async def report_completion_tool(args):
        text = await handle_report_completion(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            args=args,
        )
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "request_context",
        "Read a file from the project to get additional context",
        {"path": str},
    )
    async def request_context_tool(args):
        text = await handle_request_context(
            worktree_path=worktree_path,
            args=args,
        )
        return {"content": [{"type": "text", "text": text}]}

    return create_sdk_mcp_server(
        "jig",
        tools=[publish_message_tool, report_completion_tool, request_context_tool],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_tools.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add jig/mcp_tools.py tests/test_mcp_tools.py
git commit -m "feat: add Jig MCP server factory"
```

---

### Task 9: Agent Runner

**Files:**
- Create: `jig/agent.py`
- Create: `tests/test_agent.py`

- [ ] **Step 1: Write failing tests for run_agent**

`tests/test_agent.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jig.agent import run_agent
from jig.models import (
    AgentTypeConfig,
    Issue,
    Task,
)
from jig.persistence import save_issue, save_task


class TestRunAgent:
    @pytest.fixture
    def agent_type(self) -> AgentTypeConfig:
        return AgentTypeConfig(
            name="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )

    @pytest.fixture
    def setup_issue(self, tmp_jig_project: Path) -> tuple[Path, str, str]:
        """Create an issue and task, return (project_path, issue_id, task_id)."""
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        task = Task(
            id="task-1",
            description="Implement feature X",
            acceptance_criteria="Tests pass",
            agent_type="dev",
        )
        save_task(tmp_jig_project, "issue-1", task)
        return tmp_jig_project, "issue-1", "task-1"

    @patch("jig.agent.query")
    async def test_calls_sdk_with_correct_options(
        self, mock_query, agent_type, setup_issue
    ):
        project_path, issue_id, task_id = setup_issue

        mock_result = MagicMock()
        mock_result.result = "Done"
        mock_result.stop_reason = "end_turn"

        async def fake_query(*args, **kwargs):
            yield mock_result

        mock_query.return_value = fake_query()

        await run_agent(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            agent_type=agent_type,
            worktree_path=project_path,
        )

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        assert "Implement feature X" in call_kwargs.kwargs["prompt"]
        options = call_kwargs.kwargs["options"]
        assert "Read" in options.allowed_tools
        assert "Edit" in options.allowed_tools
        assert options.cwd == str(project_path)
        assert "You are a dev agent." in options.system_prompt

    @patch("jig.agent.query")
    async def test_returns_result_text(self, mock_query, agent_type, setup_issue):
        project_path, issue_id, task_id = setup_issue

        mock_result = MagicMock()
        mock_result.result = "Feature implemented successfully"
        mock_result.stop_reason = "end_turn"

        async def fake_query(*args, **kwargs):
            yield mock_result

        mock_query.return_value = fake_query()

        result = await run_agent(
            project_path=project_path,
            issue_id=issue_id,
            task_id=task_id,
            agent_type=agent_type,
            worktree_path=project_path,
        )

        assert result == "Feature implemented successfully"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agent.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement run_agent**

`jig/agent.py`:

```python
"""Agent runner -- spawns Claude Code agents via the SDK."""

from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

from jig.bus import MessageBus
from jig.mcp_tools import create_jig_mcp_server
from jig.models import AgentTypeConfig
from jig.persistence import load_task


async def run_agent(
    project_path: Path,
    issue_id: str,
    task_id: str,
    agent_type: AgentTypeConfig,
    worktree_path: Path,
    max_turns: int = 50,
) -> str:
    """Run a single agent on a task.

    Spawns a Claude Code agent via the SDK, configured with:
    - The agent type's system prompt and allowed tools
    - A Jig MCP server with publish_message, report_completion, request_context
    - The worktree as the working directory

    Returns the agent's final result text.
    """
    bus = MessageBus(project_path)
    task = load_task(project_path, issue_id, task_id)
    agent_name = f"{agent_type.name}-{task_id}"

    mcp_server = create_jig_mcp_server(
        bus=bus,
        project_path=project_path,
        issue_id=issue_id,
        task_id=task_id,
        agent_name=agent_name,
        worktree_path=worktree_path,
    )

    prompt = (
        f"## Task\n\n{task.description}\n\n"
        f"## Acceptance Criteria\n\n{task.acceptance_criteria}\n\n"
        f"## Instructions\n\n"
        f"Work in the current directory. When you are done, call the "
        f"report_completion tool with status 'success' and a brief reason. "
        f"If you need more information, call report_completion with status "
        f"'needs_info' and explain what you need. If you are blocked, call "
        f"report_completion with status 'blocked' and explain the blocker."
    )

    options = ClaudeAgentOptions(
        cwd=str(worktree_path),
        allowed_tools=agent_type.allowed_tools,
        disallowed_tools=agent_type.denied_tools,
        system_prompt=agent_type.system_prompt,
        mcp_servers={"jig": mcp_server},
        permission_mode="bypassPermissions",
        max_turns=max_turns,
    )

    result_text = ""
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result_text = message.result

    return result_text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_agent.py -v`
Expected: 2 passed

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add jig/agent.py tests/test_agent.py
git commit -m "feat: add agent runner (spawn via SDK with MCP tools)"
```
