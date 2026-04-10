# Agent Model Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the ephemeral agent model with persistent agent instances, structured communication/completion protocols, and a spawn-on-demand agent pool. Pure data layer — no MCP or runner changes.

**Architecture:** New Pydantic models (`AgentStatus`, `AgentInstance`, `MessageDirection`, `AgentMessage`, `CompletionStatus`, `CompletionReport`) are added to `jig/models.py`. `AgentTypeConfig` gains a `role` field replacing `name`. A new `jig/pool.py` manages the agent pool (spawn, find idle, lifecycle). Persistence gets agent instance save/load. The `WorkflowPhase` enum is removed — phases become free-form strings. `PhaseConfig` is updated to reference roles.

**Tech Stack:** Python 3.12+, Pydantic, PyYAML, pytest

**Depends on:** Plans 1-4 (existing codebase with 113 passing tests)

---

## File Structure

```
jig/
├── models.py            # (modify) Add AgentStatus, AgentInstance, MessageDirection,
│                        #   AgentMessage, CompletionStatus, CompletionReport.
│                        #   Update AgentTypeConfig, PhaseConfig. Remove WorkflowPhase enum.
├── pool.py              # (create) AgentPool — spawn, find_idle, get, list, update status
├── persistence.py       # (modify) Add agent instance save/load/list
tests/
├── test_models.py       # (modify) Add tests for new models, update existing
├── test_pool.py         # (create) AgentPool tests
├── test_persistence.py  # (modify) Add agent instance persistence tests
```

---

### Task 1: Protocol Models — AgentMessage and CompletionReport

**Files:**
- Modify: `jig/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write failing tests for AgentMessage**

Append to `tests/test_models.py`:

```python
from jig.models import MessageDirection, AgentMessage


class TestMessageDirection:
    def test_values(self):
        assert MessageDirection.REQUEST == "request"
        assert MessageDirection.RESPONSE == "response"


class TestAgentMessage:
    def test_creation(self):
        msg = AgentMessage(
            sender_id="dev-1",
            recipient_id="test-1",
            direction=MessageDirection.REQUEST,
            topic="api_design",
            content="What endpoints do we need?",
        )
        assert msg.sender_id == "dev-1"
        assert msg.recipient_id == "test-1"
        assert msg.direction == MessageDirection.REQUEST
        assert msg.topic == "api_design"
        assert msg.content == "What endpoints do we need?"
        assert msg.correlation_id is None
        assert msg.id  # auto-generated
        assert msg.timestamp  # auto-generated

    def test_with_correlation(self):
        msg = AgentMessage(
            sender_id="test-1",
            recipient_id="dev-1",
            direction=MessageDirection.RESPONSE,
            topic="api_design",
            content="We need GET /users and POST /users",
            correlation_id="corr-123",
        )
        assert msg.correlation_id == "corr-123"
        assert msg.direction == MessageDirection.RESPONSE

    def test_serialization_roundtrip(self):
        msg = AgentMessage(
            sender_id="dev-1",
            recipient_id="broadcast",
            direction=MessageDirection.REQUEST,
            topic="status_update",
            content="Starting implementation",
        )
        data = msg.model_dump(mode="json")
        restored = AgentMessage.model_validate(data)
        assert restored.sender_id == msg.sender_id
        assert restored.id == msg.id
        assert restored.topic == msg.topic
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::TestAgentMessage -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement MessageDirection and AgentMessage**

Append to `jig/models.py`:

```python
class MessageDirection(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"


class AgentMessage(BaseModel):
    """Structured message between agents."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    sender_id: str
    recipient_id: str
    direction: MessageDirection
    topic: str
    content: str
    correlation_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py::TestMessageDirection tests/test_models.py::TestAgentMessage -v`
Expected: 4 passed

- [ ] **Step 5: Write failing tests for CompletionReport**

Append to `tests/test_models.py`:

```python
from jig.models import CompletionStatus, CompletionReport


class TestCompletionStatus:
    def test_values(self):
        assert CompletionStatus.SUCCESS == "success"
        assert CompletionStatus.NEEDS_INFO == "needs_info"
        assert CompletionStatus.BLOCKED == "blocked"
        assert CompletionStatus.FAILED == "failed"


class TestCompletionReport:
    def test_success(self):
        report = CompletionReport(
            agent_id="dev-1",
            task_id="task-1",
            status=CompletionStatus.SUCCESS,
            summary="Implemented auth module",
        )
        assert report.agent_id == "dev-1"
        assert report.status == CompletionStatus.SUCCESS
        assert report.reason == ""
        assert report.artifacts == []
        assert report.needs_from is None
        assert report.question is None

    def test_needs_info(self):
        report = CompletionReport(
            agent_id="dev-1",
            task_id="task-1",
            status=CompletionStatus.NEEDS_INFO,
            summary="Cannot determine API schema",
            reason="No API spec available",
            needs_from="spec",
            question="What are the required endpoints?",
        )
        assert report.needs_from == "spec"
        assert report.question == "What are the required endpoints?"

    def test_with_artifacts(self):
        report = CompletionReport(
            agent_id="dev-1",
            task_id="task-1",
            status=CompletionStatus.SUCCESS,
            summary="Wrote tests",
            artifacts=["tests/test_auth.py", "tests/test_users.py"],
        )
        assert len(report.artifacts) == 2

    def test_serialization_roundtrip(self):
        report = CompletionReport(
            agent_id="test-1",
            task_id="task-2",
            status=CompletionStatus.FAILED,
            summary="Tests failed",
            reason="Import error in auth module",
        )
        data = report.model_dump(mode="json")
        restored = CompletionReport.model_validate(data)
        assert restored.status == CompletionStatus.FAILED
        assert restored.reason == "Import error in auth module"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::TestCompletionReport -v`
Expected: FAIL

- [ ] **Step 7: Implement CompletionStatus and CompletionReport**

Append to `jig/models.py`:

```python
class CompletionStatus(str, Enum):
    SUCCESS = "success"
    NEEDS_INFO = "needs_info"
    BLOCKED = "blocked"
    FAILED = "failed"


class CompletionReport(BaseModel):
    """Structured report when an agent finishes (or cannot finish) a task."""
    agent_id: str
    task_id: str
    status: CompletionStatus
    summary: str
    reason: str = ""
    artifacts: list[str] = []
    needs_from: str | None = None
    question: str | None = None
```

- [ ] **Step 8: Run all model tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: all passed

- [ ] **Step 9: Commit**

```bash
git add jig/models.py tests/test_models.py
git commit -m "feat: add AgentMessage and CompletionReport protocol models"
```

---

### Task 2: AgentInstance Model

**Files:**
- Modify: `jig/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_models.py`:

```python
from jig.models import AgentStatus, AgentInstance


class TestAgentStatus:
    def test_values(self):
        assert AgentStatus.IDLE == "idle"
        assert AgentStatus.ACTIVE == "active"
        assert AgentStatus.DORMANT == "dormant"


class TestAgentInstance:
    def test_defaults(self):
        instance = AgentInstance(
            id="dev-1",
            agent_type="dev",
        )
        assert instance.id == "dev-1"
        assert instance.agent_type == "dev"
        assert instance.status == AgentStatus.IDLE
        assert instance.session_id is None
        assert instance.current_task_id is None
        assert instance.memory == []

    def test_active_with_task(self):
        instance = AgentInstance(
            id="dev-1",
            agent_type="dev",
            status=AgentStatus.ACTIVE,
            session_id="session-abc",
            current_task_id="task-1",
        )
        assert instance.status == AgentStatus.ACTIVE
        assert instance.session_id == "session-abc"
        assert instance.current_task_id == "task-1"

    def test_dormant_with_memory(self):
        instance = AgentInstance(
            id="dev-1",
            agent_type="dev",
            status=AgentStatus.DORMANT,
            session_id="session-abc",
            memory=["Prefer pytest over unittest", "Project uses FastAPI"],
        )
        assert instance.status == AgentStatus.DORMANT
        assert len(instance.memory) == 2

    def test_serialization_roundtrip(self):
        instance = AgentInstance(
            id="test-2",
            agent_type="test",
            status=AgentStatus.ACTIVE,
            session_id="sess-xyz",
            current_task_id="task-5",
            memory=["Use fixtures for DB setup"],
        )
        data = instance.model_dump(mode="json")
        restored = AgentInstance.model_validate(data)
        assert restored.id == "test-2"
        assert restored.status == AgentStatus.ACTIVE
        assert restored.memory == ["Use fixtures for DB setup"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::TestAgentInstance -v`
Expected: FAIL

- [ ] **Step 3: Implement AgentStatus and AgentInstance**

Append to `jig/models.py`:

```python
class AgentStatus(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    DORMANT = "dormant"


class AgentInstance(BaseModel):
    """A running or dormant agent spawned from an agent type."""
    id: str
    agent_type: str
    status: AgentStatus = AgentStatus.IDLE
    session_id: str | None = None
    current_task_id: str | None = None
    memory: list[str] = []
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_models.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/models.py tests/test_models.py
git commit -m "feat: add AgentStatus and AgentInstance models"
```

---

### Task 3: Update AgentTypeConfig and PhaseConfig

**Files:**
- Modify: `jig/models.py`
- Modify: `jig/persistence.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_persistence.py`

This task renames `AgentTypeConfig.name` to `role`, drops `denied_tools`, removes the `WorkflowPhase` enum, and updates `PhaseConfig` to use `str` for both name and role reference.

- [ ] **Step 1: Write failing tests for updated AgentTypeConfig**

Find the existing `TestAgentTypeConfig` class in `tests/test_models.py` and replace it entirely:

```python
class TestAgentTypeConfig:
    def test_defaults(self):
        config = AgentTypeConfig(
            role="dev",
            system_prompt="You are a dev agent.",
        )
        assert config.role == "dev"
        assert config.system_prompt == "You are a dev agent."
        assert config.allowed_tools == []
        assert config.default_context == []

    def test_full_config(self):
        config = AgentTypeConfig(
            role="test",
            system_prompt="You are a test agent.",
            allowed_tools=["Read", "Bash", "Grep"],
            default_context=["issue://design", "**/*_test.py"],
        )
        assert config.allowed_tools == ["Read", "Bash", "Grep"]
        assert config.default_context == ["issue://design", "**/*_test.py"]

    def test_serialization_roundtrip(self):
        config = AgentTypeConfig(
            role="dev",
            system_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )
        data = config.model_dump()
        restored = AgentTypeConfig.model_validate(data)
        assert restored.role == config.role
        assert restored.allowed_tools == config.allowed_tools
```

- [ ] **Step 2: Run to see failure**

Run: `uv run pytest tests/test_models.py::TestAgentTypeConfig -v`
Expected: FAIL (AgentTypeConfig has `name`, not `role`)

- [ ] **Step 3: Update AgentTypeConfig**

In `jig/models.py`, replace the `AgentTypeConfig` class:

```python
class AgentTypeConfig(BaseModel):
    role: str
    system_prompt: str
    allowed_tools: list[str] = []
    default_context: list[str] = []
```

This removes `name`, `denied_tools`, and `skills` fields. The `role` field replaces `name`.

- [ ] **Step 4: Update PhaseConfig to use strings**

In `jig/models.py`, replace `PhaseConfig`:

```python
class PhaseConfig(BaseModel):
    name: str
    role: str
    task_template: str = ""
    acceptance_criteria: str = ""
```

This replaces `agent_type: str` with `role: str`, adds `task_template` and `acceptance_criteria`, and changes `name` from `WorkflowPhase` enum to plain `str`.

- [ ] **Step 5: Remove WorkflowPhase enum**

Delete the `WorkflowPhase` class from `jig/models.py`:

```python
# DELETE THIS:
class WorkflowPhase(str, Enum):
    SPEC = "spec"
    TEST = "test"
    IMPLEMENT = "implement"
    REVIEW = "review"
    VALIDATE = "validate"
    DOCUMENT = "document"
```

- [ ] **Step 6: Update persistence.py imports**

In `jig/persistence.py`, update the import to remove `WorkflowPhase` and `PhaseConfig` (PhaseConfig is still used but imported differently if needed — check). The import block should be:

```python
from jig.models import (
    AgentTypeConfig,
    Issue,
    Message,
    PhaseConfig,
    PhaseHistoryEntry,
    ProjectConfig,
    ProjectContext,
    Task,
    WorkflowConfig,
)
```

Remove `WorkflowPhase` from the import.

- [ ] **Step 7: Update save_default_agent_types in persistence.py**

Find `save_default_agent_types` and update all `AgentTypeConfig(name=...)` to `AgentTypeConfig(role=...)`. Also remove `denied_tools` if present. For example:

```python
        AgentTypeConfig(
            role="spec",
            system_prompt=(
                "You are a specification agent. Your job is to draft a design document "
                "from the issue description. Analyze requirements, identify components, "
                "and produce a clear, actionable design doc in markdown format. "
                "Use the report_completion tool when finished."
            ),
            allowed_tools=["Read", "Glob", "Grep"],
            default_context=["issue://description"],
        ),
```

Repeat for all four defaults (spec, test, dev, review), changing `name=` to `role=`.

- [ ] **Step 8: Update save_default_workflow in persistence.py**

Update the default workflow phases to use `role` instead of `agent_type`, and use plain strings for phase names:

```python
def save_default_workflow(project_path: Path) -> None:
    """Create the default v1 linear workflow."""
    workflow = WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(name="spec", role="spec",
                       task_template="Draft a design document for: {issue_title}",
                       acceptance_criteria="Design doc covers all requirements"),
            PhaseConfig(name="test", role="test",
                       task_template="Write tests based on the design doc for: {issue_title}",
                       acceptance_criteria="Tests cover specified behavior and edge cases"),
            PhaseConfig(name="implement", role="dev",
                       task_template="Implement code that passes the tests for: {issue_title}",
                       acceptance_criteria="All tests pass"),
            PhaseConfig(name="review", role="review",
                       task_template="Review implementation for: {issue_title}",
                       acceptance_criteria="No critical issues found"),
        ],
    )
    save_workflow(project_path, workflow)
```

- [ ] **Step 9: Update save_agent_type and load_agent_type**

In `save_agent_type`, the file is saved as `f"{config.name}.yaml"` — update to `f"{config.role}.yaml"`:

```python
def save_agent_type(project_path: Path, config: AgentTypeConfig) -> None:
    """Save an agent type config to .jig/agent_types/<role>.yaml."""
    type_path = _jig_dir(project_path) / "agent_types" / f"{config.role}.yaml"
    type_path.write_text(
        yaml.dump(config.model_dump(), default_flow_style=False)
    )
```

`load_agent_type` already takes a `name` parameter — keep the parameter name but it now matches the `role` field:

```python
def load_agent_type(project_path: Path, name: str) -> AgentTypeConfig:
    """Load an agent type config from .jig/agent_types/<name>.yaml."""
    type_path = _jig_dir(project_path) / "agent_types" / f"{name}.yaml"
    if not type_path.is_file():
        raise FileNotFoundError(f"Agent type '{name}' not found")
    data = yaml.safe_load(type_path.read_text())
    return AgentTypeConfig.model_validate(data)
```

- [ ] **Step 10: Fix all test references**

Update tests in `tests/test_persistence.py` that reference `AgentTypeConfig(name=...)` to use `role=`:

Find all instances of `AgentTypeConfig(name=` and change to `AgentTypeConfig(role=`. For example:

```python
# Old:
save_agent_type(tmp_jig_project, AgentTypeConfig(name="dev", system_prompt="Dev."))
# New:
save_agent_type(tmp_jig_project, AgentTypeConfig(role="dev", system_prompt="Dev."))
```

Also update `TestAgentTypePersistence` assertions: replace `loaded.name` with `loaded.role`.

Update `tests/test_persistence.py` `TestWorkflowPersistence` and `TestDefaultWorkflow` to not reference `WorkflowPhase` enum — use plain strings instead.

Update `tests/test_orchestrator.py` — any references to `WorkflowPhase.SPEC` etc. become plain strings, and `PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec")` becomes `PhaseConfig(name="spec", role="spec")`. Also update `AgentTypeConfig(name=...)` to `AgentTypeConfig(role=...)`.

Update `tests/test_cli.py` if it references `WorkflowPhase`.

- [ ] **Step 11: Fix orchestrator.py references**

In `jig/orchestrator.py`, update references:
- `phase.agent_type` becomes `phase.role`
- `phase.name.value` becomes `phase.name` (no longer an enum)

- [ ] **Step 12: Fix agent.py references**

In `jig/agent.py`, if it references `agent_type.name`, update to `agent_type.role`.

- [ ] **Step 13: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (may need to fix additional references found during the run)

- [ ] **Step 14: Commit**

```bash
git add jig/models.py jig/persistence.py jig/orchestrator.py jig/agent.py tests/
git commit -m "refactor: update AgentTypeConfig to use role, remove WorkflowPhase enum, update PhaseConfig"
```

---

### Task 4: Agent Instance Persistence

**Files:**
- Modify: `jig/persistence.py`
- Modify: `tests/test_persistence.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_persistence.py`:

```python
from jig.models import AgentInstance, AgentStatus
from jig.persistence import save_agent_instance, load_agent_instance, list_agent_instances


class TestAgentInstancePersistence:
    def test_save_and_load(self, tmp_jig_project: Path):
        instance = AgentInstance(id="dev-1", agent_type="dev")
        save_agent_instance(tmp_jig_project, instance)
        loaded = load_agent_instance(tmp_jig_project, "dev-1")
        assert loaded.id == "dev-1"
        assert loaded.agent_type == "dev"
        assert loaded.status == AgentStatus.IDLE

    def test_saves_to_correct_path(self, tmp_jig_project: Path):
        instance = AgentInstance(id="dev-1", agent_type="dev")
        save_agent_instance(tmp_jig_project, instance)
        path = tmp_jig_project / ".jig" / "agents" / "dev-1.yaml"
        assert path.is_file()

    def test_update_status(self, tmp_jig_project: Path):
        instance = AgentInstance(id="dev-1", agent_type="dev")
        save_agent_instance(tmp_jig_project, instance)
        instance.status = AgentStatus.ACTIVE
        instance.session_id = "sess-123"
        instance.current_task_id = "task-1"
        save_agent_instance(tmp_jig_project, instance)
        loaded = load_agent_instance(tmp_jig_project, "dev-1")
        assert loaded.status == AgentStatus.ACTIVE
        assert loaded.session_id == "sess-123"

    def test_list_empty(self, tmp_jig_project: Path):
        instances = list_agent_instances(tmp_jig_project)
        assert instances == []

    def test_list_multiple(self, tmp_jig_project: Path):
        save_agent_instance(tmp_jig_project, AgentInstance(id="dev-1", agent_type="dev"))
        save_agent_instance(tmp_jig_project, AgentInstance(id="dev-2", agent_type="dev"))
        save_agent_instance(tmp_jig_project, AgentInstance(id="test-1", agent_type="test"))
        instances = list_agent_instances(tmp_jig_project)
        assert len(instances) == 3
        ids = {i.id for i in instances}
        assert ids == {"dev-1", "dev-2", "test-1"}

    def test_list_by_type(self, tmp_jig_project: Path):
        save_agent_instance(tmp_jig_project, AgentInstance(id="dev-1", agent_type="dev"))
        save_agent_instance(tmp_jig_project, AgentInstance(id="dev-2", agent_type="dev"))
        save_agent_instance(tmp_jig_project, AgentInstance(id="test-1", agent_type="test"))
        dev_instances = list_agent_instances(tmp_jig_project, agent_type="dev")
        assert len(dev_instances) == 2
        assert all(i.agent_type == "dev" for i in dev_instances)

    def test_load_nonexistent_raises(self, tmp_jig_project: Path):
        with pytest.raises(FileNotFoundError):
            load_agent_instance(tmp_jig_project, "nope")

    def test_with_memory(self, tmp_jig_project: Path):
        instance = AgentInstance(
            id="dev-1",
            agent_type="dev",
            memory=["Use pytest fixtures", "Project uses FastAPI"],
        )
        save_agent_instance(tmp_jig_project, instance)
        loaded = load_agent_instance(tmp_jig_project, "dev-1")
        assert loaded.memory == ["Use pytest fixtures", "Project uses FastAPI"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_persistence.py::TestAgentInstancePersistence -v`
Expected: FAIL

- [ ] **Step 3: Implement agent instance persistence**

Add `AgentInstance` to the imports in `jig/persistence.py`:

```python
from jig.models import (
    AgentInstance,
    AgentTypeConfig,
    Issue,
    Message,
    PhaseConfig,
    PhaseHistoryEntry,
    ProjectConfig,
    ProjectContext,
    Task,
    WorkflowConfig,
)
```

Append to `jig/persistence.py`:

```python
def save_agent_instance(project_path: Path, instance: AgentInstance) -> None:
    """Save an agent instance to .jig/agents/<id>.yaml."""
    agents_dir = _jig_dir(project_path) / "agents"
    agents_dir.mkdir(exist_ok=True)
    instance_path = agents_dir / f"{instance.id}.yaml"
    instance_path.write_text(
        yaml.dump(instance.model_dump(mode="json"), default_flow_style=False)
    )


def load_agent_instance(project_path: Path, instance_id: str) -> AgentInstance:
    """Load an agent instance from .jig/agents/<id>.yaml."""
    instance_path = _jig_dir(project_path) / "agents" / f"{instance_id}.yaml"
    if not instance_path.is_file():
        raise FileNotFoundError(f"Agent instance '{instance_id}' not found")
    data = yaml.safe_load(instance_path.read_text())
    return AgentInstance.model_validate(data)


def list_agent_instances(
    project_path: Path, agent_type: str | None = None
) -> list[AgentInstance]:
    """List agent instances, optionally filtered by type."""
    agents_dir = _jig_dir(project_path) / "agents"
    if not agents_dir.is_dir():
        return []
    instances = []
    for yaml_file in sorted(agents_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text())
        instance = AgentInstance.model_validate(data)
        if agent_type is None or instance.agent_type == agent_type:
            instances.append(instance)
    return instances
```

- [ ] **Step 4: Update conftest.py fixture**

In `tests/conftest.py`, update the `tmp_jig_project` fixture to create the `agents` directory:

Add `(jig_dir / "agents").mkdir()` alongside the other directory creations.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_persistence.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add jig/persistence.py tests/test_persistence.py tests/conftest.py
git commit -m "feat: add agent instance persistence (save, load, list)"
```

---

### Task 5: Agent Pool

**Files:**
- Create: `jig/pool.py`
- Create: `tests/test_pool.py`

- [ ] **Step 1: Write failing tests**

`tests/test_pool.py`:

```python
from pathlib import Path

import pytest

from jig.models import AgentInstance, AgentStatus, AgentTypeConfig
from jig.persistence import save_agent_type, load_agent_instance, list_agent_instances
from jig.pool import AgentPool


class TestAgentPool:
    @pytest.fixture
    def pool(self, tmp_jig_project: Path) -> AgentPool:
        save_agent_type(tmp_jig_project, AgentTypeConfig(
            role="dev", system_prompt="Dev agent.", allowed_tools=["Read", "Edit"],
        ))
        save_agent_type(tmp_jig_project, AgentTypeConfig(
            role="test", system_prompt="Test agent.", allowed_tools=["Read", "Bash"],
        ))
        return AgentPool(tmp_jig_project)

    def test_spawn_creates_instance(self, pool: AgentPool, tmp_jig_project: Path):
        instance = pool.spawn("dev")
        assert instance.id.startswith("dev-")
        assert instance.agent_type == "dev"
        assert instance.status == AgentStatus.IDLE
        # Should be persisted
        loaded = load_agent_instance(tmp_jig_project, instance.id)
        assert loaded.id == instance.id

    def test_spawn_increments_ids(self, pool: AgentPool):
        i1 = pool.spawn("dev")
        i2 = pool.spawn("dev")
        assert i1.id == "dev-1"
        assert i2.id == "dev-2"

    def test_spawn_unknown_type_raises(self, pool: AgentPool):
        with pytest.raises(FileNotFoundError):
            pool.spawn("unknown")

    def test_find_idle(self, pool: AgentPool):
        pool.spawn("dev")
        instance = pool.find_idle("dev")
        assert instance is not None
        assert instance.agent_type == "dev"
        assert instance.status == AgentStatus.IDLE

    def test_find_idle_returns_none_when_all_busy(self, pool: AgentPool, tmp_jig_project: Path):
        instance = pool.spawn("dev")
        instance.status = AgentStatus.ACTIVE
        pool.update(instance)
        result = pool.find_idle("dev")
        assert result is None

    def test_find_idle_returns_none_when_no_instances(self, pool: AgentPool):
        result = pool.find_idle("dev")
        assert result is None

    def test_acquire_returns_idle_instance(self, pool: AgentPool):
        pool.spawn("dev")
        instance = pool.acquire("dev")
        assert instance.status == AgentStatus.ACTIVE

    def test_acquire_spawns_when_none_idle(self, pool: AgentPool):
        i1 = pool.spawn("dev")
        i1.status = AgentStatus.ACTIVE
        pool.update(i1)
        i2 = pool.acquire("dev")
        assert i2.id != i1.id
        assert i2.status == AgentStatus.ACTIVE

    def test_release(self, pool: AgentPool):
        instance = pool.acquire("dev")
        assert instance.status == AgentStatus.ACTIVE
        pool.release(instance)
        loaded = load_agent_instance(pool._project_path, instance.id)
        assert loaded.status == AgentStatus.DORMANT
        assert loaded.current_task_id is None

    def test_update_persists(self, pool: AgentPool, tmp_jig_project: Path):
        instance = pool.spawn("dev")
        instance.status = AgentStatus.ACTIVE
        instance.session_id = "sess-123"
        pool.update(instance)
        loaded = load_agent_instance(tmp_jig_project, instance.id)
        assert loaded.status == AgentStatus.ACTIVE
        assert loaded.session_id == "sess-123"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pool.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement AgentPool**

`jig/pool.py`:

```python
"""Agent pool — manages agent instance lifecycle."""

from pathlib import Path

from jig.models import AgentInstance, AgentStatus
from jig.persistence import (
    list_agent_instances,
    load_agent_instance,
    load_agent_type,
    save_agent_instance,
)


class AgentPool:
    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path

    def spawn(self, role: str) -> AgentInstance:
        """Create a new agent instance from a type. Returns the new instance."""
        # Verify the agent type exists
        load_agent_type(self._project_path, role)

        # Generate next ID
        existing = list_agent_instances(self._project_path, agent_type=role)
        next_num = len(existing) + 1
        instance_id = f"{role}-{next_num}"

        instance = AgentInstance(id=instance_id, agent_type=role)
        save_agent_instance(self._project_path, instance)
        return instance

    def find_idle(self, role: str) -> AgentInstance | None:
        """Find an idle agent instance with the given role. Returns None if none available."""
        instances = list_agent_instances(self._project_path, agent_type=role)
        for instance in instances:
            if instance.status == AgentStatus.IDLE:
                return instance
        return None

    def acquire(self, role: str) -> AgentInstance:
        """Get an idle agent for a role, or spawn a new one. Sets status to ACTIVE."""
        instance = self.find_idle(role)
        if instance is None:
            instance = self.spawn(role)
        instance.status = AgentStatus.ACTIVE
        save_agent_instance(self._project_path, instance)
        return instance

    def release(self, instance: AgentInstance) -> None:
        """Release an agent back to the pool as dormant."""
        instance.status = AgentStatus.DORMANT
        instance.current_task_id = None
        save_agent_instance(self._project_path, instance)

    def update(self, instance: AgentInstance) -> None:
        """Persist the current state of an agent instance."""
        save_agent_instance(self._project_path, instance)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_pool.py -v`
Expected: 10 passed

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add jig/pool.py tests/test_pool.py
git commit -m "feat: add AgentPool for spawn-on-demand agent lifecycle"
```
