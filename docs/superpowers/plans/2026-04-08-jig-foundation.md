# Jig Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the core domain models, file persistence layer, message bus, and basic CLI (`jig init`, `jig status`) so that a project can be initialized, issues created, and messages flow through the bus and persist to JSONL.

**Architecture:** Pydantic models define the domain (Project, Issue, Task, Message). A persistence module handles reading/writing `.jig/` state as YAML and JSONL. The message bus uses asyncio queues for in-process routing with JSONL as source of truth. A click-based CLI exposes `init` and `status` commands.

**Tech Stack:** Python 3.14, Pydantic, PyYAML, click, pytest, pytest-asyncio

---

## File Structure

```
jig/
├── __init__.py          # package marker
├── __main__.py          # python -m jig entry point
├── cli.py               # click CLI commands (init, status)
├── models.py            # domain models (ProjectConfig, Issue, Task, Message, enums)
├── persistence.py       # file I/O for .jig/ directory (YAML + JSONL)
└── bus.py               # async message bus (queues + file persistence)
tests/
├── conftest.py          # shared fixtures (tmp project dirs)
├── test_models.py       # model validation and serialization
├── test_persistence.py  # file read/write round-trips
├── test_bus.py          # bus publish/subscribe/persistence
└── test_cli.py          # CLI integration tests
```

---

### Task 1: Project Setup

**Files:**
- Modify: `pyproject.toml`
- Create: `jig/__init__.py`
- Create: `jig/__main__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Update pyproject.toml with dependencies and config**

```toml
[project]
name = "jig"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "click>=8.0",
]

[project.scripts]
jig = "jig.cli:cli"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync`
Expected: dependencies installed, `.venv` updated

- [ ] **Step 3: Create package files**

`jig/__init__.py`:
```python
"""Jig: Agent harness for Claude Code."""
```

`jig/__main__.py`:
```python
from jig.cli import cli

cli()
```

`tests/conftest.py`:
```python
import pytest
from pathlib import Path


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary directory simulating a git repo."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def tmp_jig_project(tmp_project: Path) -> Path:
    """Create a temporary project with .jig/ already initialized."""
    jig_dir = tmp_project / ".jig"
    jig_dir.mkdir()
    (jig_dir / "config.yaml").write_text("repo_path: .\ndefault_branch: main\n")
    (jig_dir / "issues").mkdir()
    (jig_dir / "agent_types").mkdir()
    (jig_dir / "workflows").mkdir()
    (jig_dir / "worktrees").mkdir()
    return tmp_project
```

- [ ] **Step 4: Verify setup**

Run: `uv run pytest tests/ -v --co`
Expected: collects 0 tests, no import errors

- [ ] **Step 5: Commit**

```bash
git add jig/__init__.py jig/__main__.py tests/conftest.py pyproject.toml
git commit -m "feat: initialize project structure and dependencies"
```

---

### Task 2: Domain Models

**Files:**
- Create: `jig/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing tests for enums and ProjectConfig**

`tests/test_models.py`:
```python
from jig.models import (
    ProjectConfig,
    IssueStatus,
    CompletionState,
    MessageType,
)


class TestProjectConfig:
    def test_defaults(self):
        config = ProjectConfig(repo_path="/tmp/myrepo")
        assert config.repo_path == "/tmp/myrepo"
        assert config.default_branch == "main"

    def test_custom_branch(self):
        config = ProjectConfig(repo_path="/tmp/myrepo", default_branch="develop")
        assert config.default_branch == "develop"


class TestEnums:
    def test_issue_status_values(self):
        assert IssueStatus.PENDING == "pending"
        assert IssueStatus.IN_PROGRESS == "in_progress"
        assert IssueStatus.COMPLETED == "completed"
        assert IssueStatus.FAILED == "failed"

    def test_completion_state_values(self):
        assert CompletionState.SUCCESS == "success"
        assert CompletionState.NEEDS_INFO == "needs_info"
        assert CompletionState.BLOCKED == "blocked"
        assert CompletionState.FAILED == "failed"

    def test_message_type_values(self):
        assert MessageType.TASK_ASSIGNMENT == "task_assignment"
        assert MessageType.TASK_COMPLETION == "task_completion"
        assert MessageType.QUESTION == "question"
        assert MessageType.ANSWER == "answer"
        assert MessageType.CONTEXT_UPDATE == "context_update"
        assert MessageType.STATUS == "status"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement enums and ProjectConfig**

`jig/models.py`:
```python
"""Domain models for Jig."""

from enum import Enum

from pydantic import BaseModel


class IssueStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class CompletionState(str, Enum):
    SUCCESS = "success"
    NEEDS_INFO = "needs_info"
    BLOCKED = "blocked"
    FAILED = "failed"


class MessageType(str, Enum):
    TASK_ASSIGNMENT = "task_assignment"
    TASK_COMPLETION = "task_completion"
    QUESTION = "question"
    ANSWER = "answer"
    CONTEXT_UPDATE = "context_update"
    STATUS = "status"


class ProjectConfig(BaseModel):
    repo_path: str
    default_branch: str = "main"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: 4 passed

- [ ] **Step 5: Write failing tests for Issue model**

Append to `tests/test_models.py`:
```python
from jig.models import Issue


class TestIssue:
    def test_defaults(self):
        issue = Issue(id="issue-1", title="Add auth")
        assert issue.id == "issue-1"
        assert issue.title == "Add auth"
        assert issue.status == IssueStatus.PENDING
        assert issue.current_phase is None
        assert issue.base_branch == "main"

    def test_custom_fields(self):
        issue = Issue(
            id="issue-2",
            title="Fix bug",
            status=IssueStatus.IN_PROGRESS,
            current_phase="implement",
            base_branch="develop",
        )
        assert issue.status == IssueStatus.IN_PROGRESS
        assert issue.current_phase == "implement"
        assert issue.base_branch == "develop"
```

- [ ] **Step 6: Run tests to verify new tests fail**

Run: `uv run pytest tests/test_models.py::TestIssue -v`
Expected: FAIL with `ImportError`

- [ ] **Step 7: Implement Issue model**

Append to `jig/models.py`:
```python
class Issue(BaseModel):
    id: str
    title: str
    status: IssueStatus = IssueStatus.PENDING
    current_phase: str | None = None
    base_branch: str = "main"
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: 6 passed

- [ ] **Step 9: Write failing tests for Task model**

Append to `tests/test_models.py`:
```python
from jig.models import Task


class TestTask:
    def test_defaults(self):
        task = Task(
            id="task-1",
            description="Write auth module",
            acceptance_criteria="All auth tests pass",
            agent_type="dev",
        )
        assert task.id == "task-1"
        assert task.input_context == []
        assert task.completion_state is None
        assert task.completion_reason is None

    def test_with_completion(self):
        task = Task(
            id="task-2",
            description="Review code",
            acceptance_criteria="No critical issues",
            agent_type="review",
            completion_state=CompletionState.NEEDS_INFO,
            completion_reason="Missing test coverage data",
        )
        assert task.completion_state == CompletionState.NEEDS_INFO
        assert task.completion_reason == "Missing test coverage data"
```

- [ ] **Step 10: Run tests to verify new tests fail**

Run: `uv run pytest tests/test_models.py::TestTask -v`
Expected: FAIL with `ImportError`

- [ ] **Step 11: Implement Task model**

Append to `jig/models.py`:
```python
class Task(BaseModel):
    id: str
    description: str
    acceptance_criteria: str
    agent_type: str
    input_context: list[str] = []
    completion_state: CompletionState | None = None
    completion_reason: str | None = None
```

- [ ] **Step 12: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: 8 passed

- [ ] **Step 13: Write failing tests for Message model**

Append to `tests/test_models.py`:
```python
from datetime import datetime, timezone
from jig.models import Message


class TestMessage:
    def test_defaults(self):
        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.STATUS,
        )
        assert msg.sender == "dev-agent"
        assert msg.recipient == "orchestrator"
        assert msg.type == MessageType.STATUS
        assert msg.payload == {}
        assert msg.correlation_id is None
        assert msg.id  # auto-generated
        assert msg.timestamp  # auto-generated

    def test_with_payload(self):
        msg = Message(
            sender="orchestrator",
            recipient="test-agent",
            type=MessageType.TASK_ASSIGNMENT,
            payload={"task_id": "task-1"},
            correlation_id="corr-123",
        )
        assert msg.payload == {"task_id": "task-1"}
        assert msg.correlation_id == "corr-123"

    def test_serialization_roundtrip(self):
        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.TASK_COMPLETION,
            payload={"status": "done"},
        )
        data = msg.model_dump(mode="json")
        restored = Message.model_validate(data)
        assert restored.sender == msg.sender
        assert restored.recipient == msg.recipient
        assert restored.type == msg.type
        assert restored.payload == msg.payload
        assert restored.id == msg.id
```

- [ ] **Step 14: Run tests to verify new tests fail**

Run: `uv run pytest tests/test_models.py::TestMessage -v`
Expected: FAIL with `ImportError`

- [ ] **Step 15: Implement Message model**

Append to `jig/models.py`:
```python
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import Field


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    sender: str
    recipient: str
    type: MessageType
    payload: dict = {}
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str | None = None
```

- [ ] **Step 16: Run all model tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: 11 passed

- [ ] **Step 17: Commit**

```bash
git add jig/models.py tests/test_models.py
git commit -m "feat: add domain models for project, issue, task, and message"
```

---

### Task 3: Persistence — Project Init and Config

**Files:**
- Create: `jig/persistence.py`
- Create: `tests/test_persistence.py`

- [ ] **Step 1: Write failing tests for project init**

`tests/test_persistence.py`:
```python
from pathlib import Path

from jig.models import ProjectConfig
from jig.persistence import init_project, load_project


class TestInitProject:
    def test_creates_jig_directory(self, tmp_project: Path):
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert jig_dir.is_dir()
        assert (jig_dir / "config.yaml").is_file()
        assert (jig_dir / "issues").is_dir()
        assert (jig_dir / "agent_types").is_dir()
        assert (jig_dir / "workflows").is_dir()
        assert (jig_dir / "worktrees").is_dir()

    def test_writes_config(self, tmp_project: Path):
        init_project(tmp_project, default_branch="develop")
        config = load_project(tmp_project)
        assert config.repo_path == str(tmp_project)
        assert config.default_branch == "develop"

    def test_default_branch(self, tmp_project: Path):
        init_project(tmp_project)
        config = load_project(tmp_project)
        assert config.default_branch == "main"

    def test_raises_if_already_initialized(self, tmp_jig_project: Path):
        with pytest.raises(FileExistsError):
            init_project(tmp_jig_project)

    def test_raises_if_not_git_repo(self, tmp_path: Path):
        with pytest.raises(ValueError, match="not a git repository"):
            init_project(tmp_path)
```

- [ ] **Step 2: Add missing import to test file**

Add at top of `tests/test_persistence.py`:
```python
import pytest
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_persistence.py::TestInitProject -v`
Expected: FAIL with `ImportError`

- [ ] **Step 4: Implement init_project and load_project**

`jig/persistence.py`:
```python
"""File I/O for .jig/ directory structure."""

from pathlib import Path

import yaml

from jig.models import ProjectConfig


def _jig_dir(project_path: Path) -> Path:
    return project_path / ".jig"


def init_project(project_path: Path, default_branch: str = "main") -> None:
    """Initialize .jig/ directory in a project."""
    if not (project_path / ".git").is_dir():
        raise ValueError(f"{project_path} is not a git repository")

    jig_dir = _jig_dir(project_path)
    if jig_dir.exists():
        raise FileExistsError(f"{jig_dir} already exists")

    jig_dir.mkdir()
    for subdir in ("issues", "agent_types", "workflows", "worktrees"):
        (jig_dir / subdir).mkdir()

    config = ProjectConfig(
        repo_path=str(project_path),
        default_branch=default_branch,
    )
    (jig_dir / "config.yaml").write_text(
        yaml.dump(config.model_dump(), default_flow_style=False)
    )


def load_project(project_path: Path) -> ProjectConfig:
    """Load project config from .jig/config.yaml."""
    config_path = _jig_dir(project_path) / "config.yaml"
    data = yaml.safe_load(config_path.read_text())
    return ProjectConfig.model_validate(data)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_persistence.py::TestInitProject -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add jig/persistence.py tests/test_persistence.py
git commit -m "feat: add project init and config persistence"
```

---

### Task 4: Persistence — Issues

**Files:**
- Modify: `jig/persistence.py`
- Modify: `tests/test_persistence.py`

- [ ] **Step 1: Write failing tests for issue CRUD**

Append to `tests/test_persistence.py`:
```python
from jig.models import Issue, IssueStatus
from jig.persistence import save_issue, load_issue, list_issues


class TestIssuePersistence:
    def test_save_and_load(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Add auth")
        save_issue(tmp_jig_project, issue)
        loaded = load_issue(tmp_jig_project, "issue-1")
        assert loaded.id == "issue-1"
        assert loaded.title == "Add auth"
        assert loaded.status == IssueStatus.PENDING

    def test_creates_issue_directory(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Add auth")
        save_issue(tmp_jig_project, issue)
        issue_dir = tmp_jig_project / ".jig" / "issues" / "issue-1"
        assert issue_dir.is_dir()
        assert (issue_dir / "issue.yaml").is_file()
        assert (issue_dir / "tasks").is_dir()

    def test_list_empty(self, tmp_jig_project: Path):
        issues = list_issues(tmp_jig_project)
        assert issues == []

    def test_list_multiple(self, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="a", title="First"))
        save_issue(tmp_jig_project, Issue(id="b", title="Second"))
        issues = list_issues(tmp_jig_project)
        ids = {i.id for i in issues}
        assert ids == {"a", "b"}

    def test_update_existing(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Add auth")
        save_issue(tmp_jig_project, issue)
        issue.status = IssueStatus.IN_PROGRESS
        issue.current_phase = "spec"
        save_issue(tmp_jig_project, issue)
        loaded = load_issue(tmp_jig_project, "issue-1")
        assert loaded.status == IssueStatus.IN_PROGRESS
        assert loaded.current_phase == "spec"

    def test_load_nonexistent_raises(self, tmp_jig_project: Path):
        with pytest.raises(FileNotFoundError):
            load_issue(tmp_jig_project, "nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_persistence.py::TestIssuePersistence -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement issue persistence**

Append to `jig/persistence.py`:
```python
from jig.models import Issue


def save_issue(project_path: Path, issue: Issue) -> None:
    """Save an issue to .jig/issues/<id>/issue.yaml."""
    issue_dir = _jig_dir(project_path) / "issues" / issue.id
    issue_dir.mkdir(exist_ok=True)
    (issue_dir / "tasks").mkdir(exist_ok=True)
    (issue_dir / "issue.yaml").write_text(
        yaml.dump(issue.model_dump(mode="json"), default_flow_style=False)
    )


def load_issue(project_path: Path, issue_id: str) -> Issue:
    """Load an issue from .jig/issues/<id>/issue.yaml."""
    issue_path = _jig_dir(project_path) / "issues" / issue_id / "issue.yaml"
    if not issue_path.is_file():
        raise FileNotFoundError(f"Issue {issue_id} not found")
    data = yaml.safe_load(issue_path.read_text())
    return Issue.model_validate(data)


def list_issues(project_path: Path) -> list[Issue]:
    """List all issues in .jig/issues/."""
    issues_dir = _jig_dir(project_path) / "issues"
    issues = []
    for issue_dir in sorted(issues_dir.iterdir()):
        issue_file = issue_dir / "issue.yaml"
        if issue_file.is_file():
            data = yaml.safe_load(issue_file.read_text())
            issues.append(Issue.model_validate(data))
    return issues
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_persistence.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add jig/persistence.py tests/test_persistence.py
git commit -m "feat: add issue persistence (save, load, list)"
```

---

### Task 5: Persistence — Messages (JSONL)

**Files:**
- Modify: `jig/persistence.py`
- Modify: `tests/test_persistence.py`

- [ ] **Step 1: Write failing tests for message persistence**

Append to `tests/test_persistence.py`:
```python
from jig.models import Message, MessageType
from jig.persistence import append_message, load_messages


class TestMessagePersistence:
    def test_append_and_load(self, tmp_jig_project: Path):
        # Create issue directory first
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)

        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.STATUS,
            payload={"info": "started"},
        )
        append_message(tmp_jig_project, "issue-1", msg)
        messages = load_messages(tmp_jig_project, "issue-1")
        assert len(messages) == 1
        assert messages[0].sender == "dev-agent"
        assert messages[0].payload == {"info": "started"}

    def test_append_multiple(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)

        for i in range(3):
            msg = Message(
                sender=f"agent-{i}",
                recipient="orchestrator",
                type=MessageType.STATUS,
            )
            append_message(tmp_jig_project, "issue-1", msg)
        messages = load_messages(tmp_jig_project, "issue-1")
        assert len(messages) == 3
        assert messages[0].sender == "agent-0"
        assert messages[2].sender == "agent-2"

    def test_load_empty(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)
        messages = load_messages(tmp_jig_project, "issue-1")
        assert messages == []

    def test_preserves_order(self, tmp_jig_project: Path):
        issue = Issue(id="issue-1", title="Test")
        save_issue(tmp_jig_project, issue)

        for i in range(5):
            msg = Message(
                sender="agent",
                recipient="orchestrator",
                type=MessageType.STATUS,
                payload={"seq": i},
            )
            append_message(tmp_jig_project, "issue-1", msg)
        messages = load_messages(tmp_jig_project, "issue-1")
        seqs = [m.payload["seq"] for m in messages]
        assert seqs == [0, 1, 2, 3, 4]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_persistence.py::TestMessagePersistence -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement message persistence**

Append to `jig/persistence.py`:
```python
import json

from jig.models import Message


def append_message(project_path: Path, issue_id: str, message: Message) -> None:
    """Append a message to .jig/issues/<id>/messages.jsonl."""
    messages_path = _jig_dir(project_path) / "issues" / issue_id / "messages.jsonl"
    with messages_path.open("a") as f:
        f.write(message.model_dump_json() + "\n")


def load_messages(project_path: Path, issue_id: str) -> list[Message]:
    """Load all messages from .jig/issues/<id>/messages.jsonl."""
    messages_path = _jig_dir(project_path) / "issues" / issue_id / "messages.jsonl"
    if not messages_path.is_file():
        return []
    messages = []
    for line in messages_path.read_text().splitlines():
        if line.strip():
            messages.append(Message.model_validate_json(line))
    return messages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_persistence.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add jig/persistence.py tests/test_persistence.py
git commit -m "feat: add JSONL message persistence (append, load)"
```

---

### Task 6: Message Bus

**Files:**
- Create: `jig/bus.py`
- Create: `tests/test_bus.py`

- [ ] **Step 1: Write failing tests for basic publish/subscribe**

`tests/test_bus.py`:
```python
import asyncio
from pathlib import Path

import pytest

from jig.bus import MessageBus
from jig.models import Issue, Message, MessageType
from jig.persistence import save_issue


class TestMessageBus:
    @pytest.fixture
    def bus(self, tmp_jig_project: Path) -> MessageBus:
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Test"))
        return MessageBus(tmp_jig_project)

    async def test_subscribe_and_publish(self, bus: MessageBus):
        queue = await bus.subscribe("issue-1", "orchestrator")
        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.STATUS,
        )
        await bus.publish("issue-1", msg)
        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.sender == "dev-agent"

    async def test_broadcast(self, bus: MessageBus):
        q1 = await bus.subscribe("issue-1", "agent-a")
        q2 = await bus.subscribe("issue-1", "agent-b")
        msg = Message(
            sender="orchestrator",
            recipient="broadcast",
            type=MessageType.STATUS,
        )
        await bus.publish("issue-1", msg)
        r1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert r1.id == r2.id

    async def test_targeted_delivery(self, bus: MessageBus):
        q_a = await bus.subscribe("issue-1", "agent-a")
        q_b = await bus.subscribe("issue-1", "agent-b")
        msg = Message(
            sender="orchestrator",
            recipient="agent-a",
            type=MessageType.TASK_ASSIGNMENT,
        )
        await bus.publish("issue-1", msg)
        received = await asyncio.wait_for(q_a.get(), timeout=1.0)
        assert received.sender == "orchestrator"
        assert q_b.empty()

    async def test_persists_to_jsonl(self, bus: MessageBus, tmp_jig_project: Path):
        msg = Message(
            sender="dev-agent",
            recipient="orchestrator",
            type=MessageType.TASK_COMPLETION,
            payload={"result": "done"},
        )
        await bus.publish("issue-1", msg)
        jsonl_path = tmp_jig_project / ".jig" / "issues" / "issue-1" / "messages.jsonl"
        assert jsonl_path.is_file()
        lines = jsonl_path.read_text().strip().splitlines()
        assert len(lines) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bus.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement MessageBus**

`jig/bus.py`:
```python
"""Async message bus with file-backed persistence."""

import asyncio
from pathlib import Path

from jig.models import Message
from jig.persistence import append_message


class MessageBus:
    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path
        self._subscribers: dict[str, dict[str, asyncio.Queue[Message]]] = {}

    async def subscribe(self, issue_id: str, subscriber_name: str) -> asyncio.Queue[Message]:
        """Subscribe to messages for a given issue and subscriber name."""
        if issue_id not in self._subscribers:
            self._subscribers[issue_id] = {}
        if subscriber_name not in self._subscribers[issue_id]:
            self._subscribers[issue_id][subscriber_name] = asyncio.Queue()
        return self._subscribers[issue_id][subscriber_name]

    async def publish(self, issue_id: str, message: Message) -> None:
        """Persist a message to JSONL, then route to subscribers."""
        append_message(self._project_path, issue_id, message)

        subs = self._subscribers.get(issue_id, {})
        if message.recipient == "broadcast":
            for queue in subs.values():
                await queue.put(message)
        elif message.recipient in subs:
            await subs[message.recipient].put(message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bus.py -v`
Expected: 4 passed

- [ ] **Step 5: Write failing test for replay from JSONL**

Append to `tests/test_bus.py`:
```python
    async def test_replay_from_jsonl(self, tmp_jig_project: Path):
        """A new bus instance can replay persisted messages."""
        save_issue(tmp_jig_project, Issue(id="issue-2", title="Replay test"))
        bus1 = MessageBus(tmp_jig_project)
        for i in range(3):
            msg = Message(
                sender=f"agent-{i}",
                recipient="orchestrator",
                type=MessageType.STATUS,
                payload={"seq": i},
            )
            await bus1.publish("issue-2", msg)

        bus2 = MessageBus(tmp_jig_project)
        messages = bus2.replay("issue-2")
        assert len(messages) == 3
        assert messages[0].payload["seq"] == 0
        assert messages[2].payload["seq"] == 2
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_bus.py::TestMessageBus::test_replay_from_jsonl -v`
Expected: FAIL with `AttributeError: 'MessageBus' object has no attribute 'replay'`

- [ ] **Step 7: Implement replay**

Append to the `MessageBus` class in `jig/bus.py`:
```python
    def replay(self, issue_id: str) -> list[Message]:
        """Replay all persisted messages for an issue from JSONL."""
        from jig.persistence import load_messages
        return load_messages(self._project_path, issue_id)
```

- [ ] **Step 8: Run all bus tests**

Run: `uv run pytest tests/test_bus.py -v`
Expected: 5 passed

- [ ] **Step 9: Commit**

```bash
git add jig/bus.py tests/test_bus.py
git commit -m "feat: add async message bus with file-backed persistence"
```

---

### Task 7: CLI — jig init

**Files:**
- Create: `jig/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for jig init**

`tests/test_cli.py`:
```python
from pathlib import Path

from click.testing import CliRunner

from jig.cli import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestInit:
    def test_initializes_project(self, runner: CliRunner, tmp_project: Path):
        result = runner.invoke(cli, ["init", "--path", str(tmp_project)])
        assert result.exit_code == 0
        assert (tmp_project / ".jig").is_dir()
        assert "Initialized" in result.output

    def test_custom_branch(self, runner: CliRunner, tmp_project: Path):
        result = runner.invoke(
            cli, ["init", "--path", str(tmp_project), "--branch", "develop"]
        )
        assert result.exit_code == 0
        config_text = (tmp_project / ".jig" / "config.yaml").read_text()
        assert "develop" in config_text

    def test_already_initialized(self, runner: CliRunner, tmp_jig_project: Path):
        result = runner.invoke(cli, ["init", "--path", str(tmp_jig_project)])
        assert result.exit_code != 0
        assert "already" in result.output.lower()

    def test_not_git_repo(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(cli, ["init", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "git" in result.output.lower()
```

- [ ] **Step 2: Add missing import**

Add at top of `tests/test_cli.py`:
```python
import pytest
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::TestInit -v`
Expected: FAIL with `ImportError`

- [ ] **Step 4: Implement CLI with init command**

`jig/cli.py`:
```python
"""Jig CLI."""

from pathlib import Path

import click

from jig.persistence import init_project


@click.group()
def cli() -> None:
    """Jig: Agent harness for Claude Code."""


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--branch", default="main", help="Default branch name.")
def init(path: Path, branch: str) -> None:
    """Initialize .jig/ in a project."""
    try:
        init_project(path, default_branch=branch)
        click.echo(f"Initialized Jig in {path / '.jig'}")
    except FileExistsError:
        raise click.ClickException(f"Already initialized: {path / '.jig'}")
    except ValueError as e:
        raise click.ClickException(str(e))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py::TestInit -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add jig/cli.py tests/test_cli.py
git commit -m "feat: add jig init CLI command"
```

---

### Task 8: CLI — jig status

**Files:**
- Modify: `jig/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for jig status**

Append to `tests/test_cli.py`:
```python
from jig.models import Issue, IssueStatus
from jig.persistence import save_issue


class TestStatus:
    def test_no_issues(self, runner: CliRunner, tmp_jig_project: Path):
        result = runner.invoke(cli, ["status", "--path", str(tmp_jig_project)])
        assert result.exit_code == 0
        assert "No issues" in result.output

    def test_with_issues(self, runner: CliRunner, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Add auth"))
        save_issue(
            tmp_jig_project,
            Issue(
                id="issue-2",
                title="Fix bug",
                status=IssueStatus.IN_PROGRESS,
                current_phase="implement",
            ),
        )
        result = runner.invoke(cli, ["status", "--path", str(tmp_jig_project)])
        assert result.exit_code == 0
        assert "issue-1" in result.output
        assert "Add auth" in result.output
        assert "pending" in result.output
        assert "issue-2" in result.output
        assert "Fix bug" in result.output
        assert "in_progress" in result.output
        assert "implement" in result.output

    def test_not_initialized(self, runner: CliRunner, tmp_project: Path):
        result = runner.invoke(cli, ["status", "--path", str(tmp_project)])
        assert result.exit_code != 0
        assert "not initialized" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::TestStatus -v`
Expected: FAIL with `UsageError` or missing command

- [ ] **Step 3: Implement status command**

Append to `jig/cli.py`:
```python
from jig.persistence import list_issues, load_project


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def status(path: Path) -> None:
    """Show current workflow state."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    config = load_project(path)
    issues = list_issues(path)

    click.echo(f"Project: {config.repo_path}")
    click.echo(f"Branch:  {config.default_branch}")
    click.echo()

    if not issues:
        click.echo("No issues.")
        return

    click.echo(f"{'ID':<15} {'Title':<30} {'Status':<15} {'Phase':<15}")
    click.echo("-" * 75)
    for issue in issues:
        phase = issue.current_phase or "-"
        click.echo(f"{issue.id:<15} {issue.title:<30} {issue.status.value:<15} {phase:<15}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (should be ~27 tests)

- [ ] **Step 6: Commit**

```bash
git add jig/cli.py tests/test_cli.py
git commit -m "feat: add jig status CLI command"
```

---

### Task 9: Delete Boilerplate

**Files:**
- Delete: `main.py`

- [ ] **Step 1: Remove PyCharm boilerplate**

```bash
rm main.py
```

- [ ] **Step 2: Run full test suite to confirm nothing broke**

Run: `uv run pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove PyCharm boilerplate main.py"
```