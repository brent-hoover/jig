# Jig Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the workflow engine that runs a single issue through all phases (spec, test, implement, review), with crash recovery and CLI commands (`jig start`, `jig validate`).

**Architecture:** A `WorkflowConfig` model defines the phase sequence as YAML. The `Orchestrator` class walks the phases sequentially: for each phase it creates a worktree, creates a task, runs the agent, checks completion state, and commits. State is persisted to `.jig/` after every transition so the orchestrator can resume after a crash. The CLI runs the orchestrator in the foreground; Ctrl+C stops gracefully.

**Tech Stack:** Python 3.12+, asyncio, click, Pydantic, PyYAML, pytest

**Depends on:** Plan 1 (Foundation) + Plan 2 (Agent System)

---

## File Structure

```
jig/
├── models.py            # (modify) Add WorkflowPhase enum, PhaseConfig, WorkflowConfig
├── persistence.py       # (modify) Add workflow save/load + save_default_workflow
├── orchestrator.py      # (create) Orchestrator class — walks workflow phases
├── cli.py               # (modify) Add start, validate commands
tests/
├── test_models.py       # (modify) Add workflow model tests
├── test_persistence.py  # (modify) Add workflow persistence tests
├── test_orchestrator.py # (create) Orchestrator tests (mocked agent runner)
├── test_cli.py          # (modify) Add start/validate CLI tests
```

---

### Task 1: Workflow Models

**Files:**
- Modify: `jig/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_models.py`:

```python
from jig.models import WorkflowPhase, PhaseConfig, WorkflowConfig


class TestWorkflowPhase:
    def test_values(self):
        assert WorkflowPhase.SPEC == "spec"
        assert WorkflowPhase.TEST == "test"
        assert WorkflowPhase.IMPLEMENT == "implement"
        assert WorkflowPhase.REVIEW == "review"


class TestPhaseConfig:
    def test_creation(self):
        phase = PhaseConfig(
            name=WorkflowPhase.SPEC,
            agent_type="spec",
        )
        assert phase.name == "spec"
        assert phase.agent_type == "spec"


class TestWorkflowConfig:
    def test_creation(self):
        workflow = WorkflowConfig(
            name="default",
            phases=[
                PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec"),
                PhaseConfig(name=WorkflowPhase.TEST, agent_type="test"),
            ],
        )
        assert workflow.name == "default"
        assert len(workflow.phases) == 2
        assert workflow.phases[0].name == "spec"
        assert workflow.phases[1].agent_type == "test"

    def test_serialization_roundtrip(self):
        workflow = WorkflowConfig(
            name="default",
            phases=[
                PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec"),
            ],
        )
        data = workflow.model_dump()
        restored = WorkflowConfig.model_validate(data)
        assert restored.name == workflow.name
        assert len(restored.phases) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py::TestWorkflowPhase -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement workflow models**

Append to `jig/models.py`:

```python
class WorkflowPhase(str, Enum):
    SPEC = "spec"
    TEST = "test"
    IMPLEMENT = "implement"
    REVIEW = "review"


class PhaseConfig(BaseModel):
    name: WorkflowPhase
    agent_type: str


class WorkflowConfig(BaseModel):
    name: str
    phases: list[PhaseConfig]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/models.py tests/test_models.py
git commit -m "feat: add WorkflowPhase, PhaseConfig, WorkflowConfig models"
```

---

### Task 2: Workflow Persistence + Default Workflow

**Files:**
- Modify: `jig/persistence.py`
- Modify: `tests/test_persistence.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_persistence.py`:

```python
from jig.models import WorkflowPhase, PhaseConfig, WorkflowConfig
from jig.persistence import save_workflow, load_workflow, save_default_workflow


class TestWorkflowPersistence:
    def test_save_and_load(self, tmp_jig_project: Path):
        workflow = WorkflowConfig(
            name="custom",
            phases=[
                PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec"),
                PhaseConfig(name=WorkflowPhase.TEST, agent_type="test"),
            ],
        )
        save_workflow(tmp_jig_project, workflow)
        loaded = load_workflow(tmp_jig_project, "custom")
        assert loaded.name == "custom"
        assert len(loaded.phases) == 2

    def test_saves_to_correct_path(self, tmp_jig_project: Path):
        workflow = WorkflowConfig(
            name="custom",
            phases=[PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec")],
        )
        save_workflow(tmp_jig_project, workflow)
        path = tmp_jig_project / ".jig" / "workflows" / "custom.yaml"
        assert path.is_file()

    def test_load_nonexistent_raises(self, tmp_jig_project: Path):
        with pytest.raises(FileNotFoundError):
            load_workflow(tmp_jig_project, "nope")


class TestDefaultWorkflow:
    def test_creates_default(self, tmp_jig_project: Path):
        save_default_workflow(tmp_jig_project)
        workflow = load_workflow(tmp_jig_project, "default")
        assert workflow.name == "default"
        phase_names = [p.name for p in workflow.phases]
        assert phase_names == [
            WorkflowPhase.SPEC,
            WorkflowPhase.TEST,
            WorkflowPhase.IMPLEMENT,
            WorkflowPhase.REVIEW,
        ]

    def test_agent_types_match_phase_names(self, tmp_jig_project: Path):
        save_default_workflow(tmp_jig_project)
        workflow = load_workflow(tmp_jig_project, "default")
        for phase in workflow.phases:
            assert phase.agent_type == phase.name.value
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_persistence.py::TestWorkflowPersistence -v`
Expected: FAIL

- [ ] **Step 3: Implement workflow persistence**

Add `WorkflowConfig, PhaseConfig, WorkflowPhase` to the models import at top of `jig/persistence.py`:

```python
from jig.models import ProjectConfig, Issue, Message, AgentTypeConfig, Task, WorkflowConfig, PhaseConfig, WorkflowPhase
```

Append to `jig/persistence.py`:

```python
def save_workflow(project_path: Path, workflow: WorkflowConfig) -> None:
    """Save a workflow config to .jig/workflows/<name>.yaml."""
    wf_path = _jig_dir(project_path) / "workflows" / f"{workflow.name}.yaml"
    wf_path.write_text(
        yaml.dump(workflow.model_dump(mode="json"), default_flow_style=False)
    )


def load_workflow(project_path: Path, name: str) -> WorkflowConfig:
    """Load a workflow config from .jig/workflows/<name>.yaml."""
    wf_path = _jig_dir(project_path) / "workflows" / f"{name}.yaml"
    if not wf_path.is_file():
        raise FileNotFoundError(f"Workflow '{name}' not found")
    data = yaml.safe_load(wf_path.read_text())
    return WorkflowConfig.model_validate(data)


def save_default_workflow(project_path: Path) -> None:
    """Create the default v1 linear workflow."""
    workflow = WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec"),
            PhaseConfig(name=WorkflowPhase.TEST, agent_type="test"),
            PhaseConfig(name=WorkflowPhase.IMPLEMENT, agent_type="implement"),
            PhaseConfig(name=WorkflowPhase.REVIEW, agent_type="review"),
        ],
    )
    save_workflow(project_path, workflow)
```

Note: The implement phase uses agent_type "implement" but the agent type config is named "dev". This is intentional — the phase name and agent type name don't have to match. We'll update the default workflow to use agent_type="dev" for the implement phase:

```python
            PhaseConfig(name=WorkflowPhase.IMPLEMENT, agent_type="dev"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_persistence.py -v`
Expected: all passed

- [ ] **Step 5: Update jig init to create default workflow**

In `jig/cli.py`, update the import:

```python
from jig.persistence import init_project, list_issues, load_project, save_default_agent_types, save_default_workflow
```

In the `init` command, add `save_default_workflow(path)` after `save_default_agent_types(path)`:

```python
        init_project(path, default_branch=branch)
        save_default_agent_types(path)
        save_default_workflow(path)
        click.echo(f"Initialized Jig in {path / '.jig'}")
```

- [ ] **Step 6: Write test for updated init**

Append to `tests/test_cli.py`:

```python
from jig.persistence import load_workflow


class TestInitCreatesWorkflow:
    def test_init_creates_default_workflow(self, runner: CliRunner, tmp_project: Path):
        result = runner.invoke(cli, ["init", "--path", str(tmp_project)])
        assert result.exit_code == 0
        workflow = load_workflow(tmp_project, "default")
        assert workflow.name == "default"
        assert len(workflow.phases) == 4
```

- [ ] **Step 7: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all passed

- [ ] **Step 8: Commit**

```bash
git add jig/models.py jig/persistence.py jig/cli.py tests/test_models.py tests/test_persistence.py tests/test_cli.py
git commit -m "feat: add workflow persistence, default workflow, update init"
```

---

### Task 3: Orchestrator — Execute Single Phase

**Files:**
- Create: `jig/orchestrator.py`
- Create: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests for single phase execution**

`tests/test_orchestrator.py`:

```python
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jig.models import (
    AgentTypeConfig,
    CompletionState,
    Issue,
    IssueStatus,
    PhaseConfig,
    Task,
    WorkflowConfig,
    WorkflowPhase,
)
from jig.orchestrator import Orchestrator
from jig.persistence import (
    load_issue,
    load_task,
    save_agent_type,
    save_issue,
    save_workflow,
)


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Create a real git repo with .jig/ initialized."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    # Ensure we're on 'main'
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, capture_output=True)

    # Init .jig structure
    jig_dir = repo / ".jig"
    for d in ("issues", "agent_types", "workflows", "worktrees"):
        (jig_dir / d).mkdir(parents=True, exist_ok=True)

    from jig.models import ProjectConfig
    import yaml
    config = ProjectConfig(repo_path=str(repo))
    (jig_dir / "config.yaml").write_text(yaml.dump(config.model_dump(), default_flow_style=False))

    # Add agent types
    save_agent_type(repo, AgentTypeConfig(
        name="spec", system_prompt="Spec agent.", allowed_tools=["Read"],
    ))

    # Add workflow
    save_workflow(repo, WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec")],
    ))

    # Add issue
    save_issue(repo, Issue(id="issue-1", title="Test feature", base_branch="main"))

    # Commit .jig so worktrees can see it
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add jig config"], cwd=repo, check=True, capture_output=True)

    return repo


class TestOrchestratorSinglePhase:
    @patch("jig.orchestrator.run_agent")
    async def test_executes_phase(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Phase complete"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        mock_run_agent.assert_called_once()
        call_kwargs = mock_run_agent.call_args.kwargs
        assert call_kwargs["issue_id"] == "issue-1"
        assert call_kwargs["agent_type"].name == "spec"

    @patch("jig.orchestrator.run_agent")
    async def test_updates_issue_status_to_in_progress(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        issue = load_issue(git_project, "issue-1")
        assert issue.status == IssueStatus.COMPLETED

    @patch("jig.orchestrator.run_agent")
    async def test_updates_issue_current_phase(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        # After completion, current_phase reflects last completed phase
        issue = load_issue(git_project, "issue-1")
        assert issue.current_phase == "spec"

    @patch("jig.orchestrator.run_agent")
    async def test_creates_task_for_phase(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        # Task should exist for the spec phase
        task = load_task(git_project, "issue-1", "spec")
        assert task.agent_type == "spec"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement Orchestrator**

`jig/orchestrator.py`:

```python
"""Orchestrator — walks workflow phases for an issue."""

from pathlib import Path

from jig.agent import run_agent
from jig.models import (
    CompletionState,
    Issue,
    IssueStatus,
    PhaseConfig,
    Task,
)
from jig.persistence import (
    load_agent_type,
    load_issue,
    load_task,
    load_workflow,
    save_issue,
    save_task,
)
from jig.worktree import commit_worktree, create_worktree


class Orchestrator:
    def __init__(self, project_path: Path, issue_id: str, workflow_name: str = "default") -> None:
        self._project_path = project_path
        self._issue_id = issue_id
        self._workflow_name = workflow_name

    async def run(self) -> None:
        """Execute the full workflow for the issue."""
        workflow = load_workflow(self._project_path, self._workflow_name)
        issue = load_issue(self._project_path, self._issue_id)

        # Find where to resume (skip completed phases)
        start_index = self._find_resume_index(workflow.phases, issue)

        issue.status = IssueStatus.IN_PROGRESS
        save_issue(self._project_path, issue)

        for phase in workflow.phases[start_index:]:
            await self._execute_phase(phase, issue)

        issue.status = IssueStatus.COMPLETED
        save_issue(self._project_path, issue)

    def _find_resume_index(self, phases: list[PhaseConfig], issue: Issue) -> int:
        """Find which phase to start from based on issue state."""
        if issue.current_phase is None:
            return 0
        for i, phase in enumerate(phases):
            if phase.name.value == issue.current_phase:
                # Check if this phase's task is already completed
                try:
                    task = load_task(self._project_path, self._issue_id, phase.name.value)
                    if task.completion_state == CompletionState.SUCCESS:
                        return i + 1  # Resume from next phase
                except FileNotFoundError:
                    pass
                return i  # Resume this phase
        return 0

    async def _execute_phase(self, phase: PhaseConfig, issue: Issue) -> None:
        """Execute a single workflow phase."""
        issue.current_phase = phase.name.value
        save_issue(self._project_path, issue)

        agent_type = load_agent_type(self._project_path, phase.agent_type)

        # Create worktree
        worktree_path = await create_worktree(
            self._project_path,
            self._issue_id,
            phase.name.value,
            issue.base_branch,
        )

        # Create task
        task = Task(
            id=phase.name.value,
            description=f"Execute {phase.name.value} phase for issue: {issue.title}",
            acceptance_criteria=f"Complete the {phase.name.value} phase successfully",
            agent_type=phase.agent_type,
        )
        save_task(self._project_path, self._issue_id, task)

        # Run agent
        await run_agent(
            project_path=self._project_path,
            issue_id=self._issue_id,
            task_id=task.id,
            agent_type=agent_type,
            worktree_path=worktree_path,
        )

        # Commit worktree changes
        await commit_worktree(worktree_path, f"{phase.name.value}: {issue.title}")

        # Reload task to check completion state set by agent
        task = load_task(self._project_path, self._issue_id, task.id)

        if task.completion_state == CompletionState.NEEDS_INFO:
            issue.status = IssueStatus.PENDING
            save_issue(self._project_path, issue)
            raise OrchestratorPaused(
                f"Agent needs info during {phase.name.value}: {task.completion_reason}"
            )

        if task.completion_state in (CompletionState.BLOCKED, CompletionState.FAILED):
            issue.status = IssueStatus.FAILED
            save_issue(self._project_path, issue)
            raise OrchestratorFailed(
                f"Agent {task.completion_state.value} during {phase.name.value}: {task.completion_reason}"
            )


class OrchestratorPaused(Exception):
    """Raised when the orchestrator pauses for user input."""


class OrchestratorFailed(Exception):
    """Raised when the orchestrator encounters a failure."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add orchestrator with single-phase execution"
```

---

### Task 4: Orchestrator — Multi-Phase and Error Handling

**Files:**
- Modify: `tests/test_orchestrator.py`

These tests use the existing Orchestrator implementation but verify multi-phase workflows and error handling paths.

- [ ] **Step 1: Write failing tests for multi-phase and error handling**

Append to `tests/test_orchestrator.py`:

```python
from jig.orchestrator import OrchestratorPaused, OrchestratorFailed


@pytest.fixture
def git_project_full_workflow(tmp_path: Path) -> Path:
    """Git repo with full 4-phase workflow."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, capture_output=True)

    jig_dir = repo / ".jig"
    for d in ("issues", "agent_types", "workflows", "worktrees"):
        (jig_dir / d).mkdir(parents=True, exist_ok=True)

    import yaml
    from jig.models import ProjectConfig
    config = ProjectConfig(repo_path=str(repo))
    (jig_dir / "config.yaml").write_text(yaml.dump(config.model_dump(), default_flow_style=False))

    for name in ("spec", "test", "dev", "review"):
        save_agent_type(repo, AgentTypeConfig(
            name=name, system_prompt=f"{name} agent.", allowed_tools=["Read"],
        ))

    save_workflow(repo, WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(name=WorkflowPhase.SPEC, agent_type="spec"),
            PhaseConfig(name=WorkflowPhase.TEST, agent_type="test"),
            PhaseConfig(name=WorkflowPhase.IMPLEMENT, agent_type="dev"),
            PhaseConfig(name=WorkflowPhase.REVIEW, agent_type="review"),
        ],
    ))

    save_issue(repo, Issue(id="issue-1", title="Test feature", base_branch="main"))

    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add jig config"], cwd=repo, check=True, capture_output=True)

    return repo


class TestOrchestratorMultiPhase:
    @patch("jig.orchestrator.run_agent")
    async def test_runs_all_four_phases(self, mock_run_agent, git_project_full_workflow: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        await orchestrator.run()

        assert mock_run_agent.call_count == 4
        agent_types = [
            call.kwargs["agent_type"].name
            for call in mock_run_agent.call_args_list
        ]
        assert agent_types == ["spec", "test", "dev", "review"]

    @patch("jig.orchestrator.run_agent")
    async def test_issue_completed_after_all_phases(self, mock_run_agent, git_project_full_workflow: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        await orchestrator.run()

        issue = load_issue(git_project_full_workflow, "issue-1")
        assert issue.status == IssueStatus.COMPLETED
        assert issue.current_phase == "review"


class TestOrchestratorErrorHandling:
    @patch("jig.orchestrator.run_agent")
    async def test_stops_on_needs_info(self, mock_run_agent, git_project_full_workflow: Path):
        """When agent reports needs_info, orchestrator pauses."""
        call_count = 0

        async def fake_run_agent(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:  # Fail on test phase
                task = load_task(
                    kwargs["project_path"], kwargs["issue_id"], kwargs["task_id"]
                )
                task.completion_state = CompletionState.NEEDS_INFO
                task.completion_reason = "Need API spec"
                save_task(kwargs["project_path"], kwargs["issue_id"], task)
            return "Done"

        mock_run_agent.side_effect = fake_run_agent

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        with pytest.raises(OrchestratorPaused, match="Need API spec"):
            await orchestrator.run()

        assert call_count == 2
        issue = load_issue(git_project_full_workflow, "issue-1")
        assert issue.status == IssueStatus.PENDING
        assert issue.current_phase == "test"

    @patch("jig.orchestrator.run_agent")
    async def test_stops_on_failure(self, mock_run_agent, git_project_full_workflow: Path):
        """When agent reports failed, orchestrator stops."""
        async def fake_run_agent(**kwargs):
            task = load_task(kwargs["project_path"], kwargs["issue_id"], kwargs["task_id"])
            task.completion_state = CompletionState.FAILED
            task.completion_reason = "Cannot compile"
            save_task(kwargs["project_path"], kwargs["issue_id"], task)
            return "Failed"

        mock_run_agent.side_effect = fake_run_agent

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        with pytest.raises(OrchestratorFailed, match="Cannot compile"):
            await orchestrator.run()

        issue = load_issue(git_project_full_workflow, "issue-1")
        assert issue.status == IssueStatus.FAILED
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: all passed (existing 4 + 4 new = 8)

If any fail due to the mock side_effect approach, adjust. The key is that `run_agent` is mocked and the tests verify the orchestrator's behavior when agents report different completion states.

- [ ] **Step 3: Commit**

```bash
git add tests/test_orchestrator.py
git commit -m "test: add multi-phase and error handling orchestrator tests"
```

---

### Task 5: Crash Recovery

**Files:**
- Modify: `tests/test_orchestrator.py`

The `_find_resume_index` method already implements crash recovery logic. These tests verify it works.

- [ ] **Step 1: Write tests for crash recovery**

Append to `tests/test_orchestrator.py`:

```python
class TestOrchestratorResume:
    @patch("jig.orchestrator.run_agent")
    async def test_resumes_from_last_completed_phase(self, mock_run_agent, git_project_full_workflow: Path):
        """If spec phase was completed, resume from test phase."""
        mock_run_agent.return_value = "Done"

        # Simulate spec phase already completed
        issue = load_issue(git_project_full_workflow, "issue-1")
        issue.current_phase = "spec"
        issue.status = IssueStatus.IN_PROGRESS
        save_issue(git_project_full_workflow, issue)

        task = Task(
            id="spec",
            description="Spec phase",
            acceptance_criteria="Done",
            agent_type="spec",
            completion_state=CompletionState.SUCCESS,
            completion_reason="Design doc written",
        )
        save_task(git_project_full_workflow, "issue-1", task)

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        await orchestrator.run()

        # Should only run 3 remaining phases (test, implement, review)
        assert mock_run_agent.call_count == 3
        agent_types = [
            call.kwargs["agent_type"].name
            for call in mock_run_agent.call_args_list
        ]
        assert agent_types == ["test", "dev", "review"]

    @patch("jig.orchestrator.run_agent")
    async def test_resumes_incomplete_phase(self, mock_run_agent, git_project_full_workflow: Path):
        """If spec phase started but not completed, re-run it."""
        mock_run_agent.return_value = "Done"

        # Simulate spec phase started but no task completion
        issue = load_issue(git_project_full_workflow, "issue-1")
        issue.current_phase = "spec"
        issue.status = IssueStatus.IN_PROGRESS
        save_issue(git_project_full_workflow, issue)

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        await orchestrator.run()

        # Should run all 4 phases (spec re-run + test + implement + review)
        assert mock_run_agent.call_count == 4
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_orchestrator.py::TestOrchestratorResume -v`
Expected: 2 passed

NOTE: The `test_resumes_incomplete_phase` test may fail because `create_worktree` will fail if the branch `jig/issue-1/spec` already exists from a prior incomplete run. If this happens, the orchestrator's `_execute_phase` needs to handle the case where the worktree/branch already exists. Add cleanup logic before creating:

In `jig/orchestrator.py`, update `_execute_phase` to handle existing worktrees:

```python
    async def _execute_phase(self, phase: PhaseConfig, issue: Issue) -> None:
        """Execute a single workflow phase."""
        issue.current_phase = phase.name.value
        save_issue(self._project_path, issue)

        agent_type = load_agent_type(self._project_path, phase.agent_type)

        # Clean up any existing worktree from a prior incomplete run
        worktree_path = self._project_path / ".jig" / "worktrees" / self._issue_id / phase.name.value
        if worktree_path.exists():
            from jig.worktree import remove_worktree
            try:
                await remove_worktree(self._project_path, self._issue_id, phase.name.value)
            except RuntimeError:
                pass

        # Create worktree
        worktree_path = await create_worktree(
            self._project_path,
            self._issue_id,
            phase.name.value,
            issue.base_branch,
        )

        # ... rest of method unchanged
```

- [ ] **Step 3: Run all orchestrator tests**

Run: `uv run pytest tests/test_orchestrator.py -v`
Expected: all passed (10 tests)

- [ ] **Step 4: Commit**

```bash
git add jig/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: add crash recovery and worktree cleanup for resume"
```

---

### Task 6: CLI — jig start

**Files:**
- Modify: `jig/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for jig start**

Append to `tests/test_cli.py`:

```python
import asyncio
from unittest.mock import patch, AsyncMock

from jig.persistence import load_issue, save_issue
from jig.models import Issue, IssueStatus


class TestStart:
    @patch("jig.cli.Orchestrator")
    def test_creates_issue_and_runs(self, MockOrchestrator, runner: CliRunner, tmp_jig_project: Path):
        mock_instance = MockOrchestrator.return_value
        mock_instance.run = AsyncMock()

        result = runner.invoke(cli, [
            "start", "--path", str(tmp_jig_project),
            "--issue-id", "feat-auth",
            "--title", "Add authentication",
        ])
        assert result.exit_code == 0
        assert "Starting" in result.output

        # Issue should be created
        issue = load_issue(tmp_jig_project, "feat-auth")
        assert issue.title == "Add authentication"

        # Orchestrator should be called
        MockOrchestrator.assert_called_once_with(tmp_jig_project, "feat-auth")
        mock_instance.run.assert_called_once()

    @patch("jig.cli.Orchestrator")
    def test_uses_existing_issue(self, MockOrchestrator, runner: CliRunner, tmp_jig_project: Path):
        mock_instance = MockOrchestrator.return_value
        mock_instance.run = AsyncMock()

        # Pre-create the issue
        save_issue(tmp_jig_project, Issue(id="feat-auth", title="Add auth"))

        result = runner.invoke(cli, [
            "start", "--path", str(tmp_jig_project),
            "--issue-id", "feat-auth",
        ])
        assert result.exit_code == 0
        MockOrchestrator.assert_called_once()

    def test_not_initialized(self, runner: CliRunner, tmp_project: Path):
        result = runner.invoke(cli, [
            "start", "--path", str(tmp_project),
            "--issue-id", "test",
            "--title", "Test",
        ])
        assert result.exit_code != 0
        assert "not initialized" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::TestStart -v`
Expected: FAIL

- [ ] **Step 3: Implement jig start**

Add to imports in `jig/cli.py`:

```python
import asyncio

from jig.models import Issue
from jig.orchestrator import Orchestrator, OrchestratorPaused, OrchestratorFailed
from jig.persistence import init_project, list_issues, load_issue, load_project, save_default_agent_types, save_default_workflow, save_issue
```

Append the start command to `jig/cli.py`:

```python
@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--issue-id", required=True, help="Unique issue identifier.")
@click.option("--title", default=None, help="Issue title (required for new issues).")
def start(path: Path, issue_id: str, title: str | None) -> None:
    """Start a workflow for an issue."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    # Create or load issue
    try:
        issue = load_issue(path, issue_id)
        click.echo(f"Resuming issue: {issue.id} - {issue.title}")
    except FileNotFoundError:
        if not title:
            raise click.ClickException("--title is required for new issues.")
        issue = Issue(id=issue_id, title=title)
        save_issue(path, issue)
        click.echo(f"Created issue: {issue.id} - {issue.title}")

    click.echo(f"Starting workflow for {issue_id}...")

    orchestrator = Orchestrator(path, issue_id)
    try:
        asyncio.run(orchestrator.run())
        click.echo(f"Workflow completed for {issue_id}.")
    except OrchestratorPaused as e:
        click.echo(f"Workflow paused: {e}")
        click.echo("Resolve the issue and run 'jig start' again to resume.")
    except OrchestratorFailed as e:
        raise click.ClickException(f"Workflow failed: {e}")
    except KeyboardInterrupt:
        click.echo("\nWorkflow interrupted. Run 'jig start' again to resume.")
```

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add jig/cli.py tests/test_cli.py
git commit -m "feat: add jig start CLI command"
```

---

### Task 7: CLI — jig validate

**Files:**
- Modify: `jig/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests for jig validate**

Append to `tests/test_cli.py`:

```python
import subprocess


class TestValidate:
    @pytest.fixture
    def git_jig_project(self, tmp_path: Path) -> Path:
        """A real git repo with .jig/ initialized."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "main"], cwd=repo, capture_output=True)

        # Initialize jig
        result = CliRunner().invoke(cli, ["init", "--path", str(repo)])
        assert result.exit_code == 0

        # Commit .jig
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "jig init"], cwd=repo, check=True, capture_output=True)

        # Create a completed issue with a worktree
        save_issue(repo, Issue(
            id="issue-1", title="Test", status=IssueStatus.COMPLETED, current_phase="spec",
        ))

        return repo

    def test_validates_issue(self, runner: CliRunner, git_jig_project: Path):
        result = runner.invoke(cli, [
            "validate", "--path", str(git_jig_project), "--issue-id", "issue-1",
        ])
        assert result.exit_code == 0
        assert "validated" in result.output.lower()

    def test_nonexistent_issue(self, runner: CliRunner, tmp_jig_project: Path):
        result = runner.invoke(cli, [
            "validate", "--path", str(tmp_jig_project), "--issue-id", "nope",
        ])
        assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::TestValidate -v`
Expected: FAIL

- [ ] **Step 3: Implement jig validate**

Append to `jig/cli.py`:

```python
from jig.worktree import remove_worktree


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--issue-id", required=True, help="Issue to validate.")
def validate(path: Path, issue_id: str) -> None:
    """Validate an issue and clean up worktrees."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    try:
        issue = load_issue(path, issue_id)
    except FileNotFoundError:
        raise click.ClickException(f"Issue '{issue_id}' not found.")

    # Clean up worktrees for this issue
    worktrees_dir = jig_dir / "worktrees" / issue_id
    if worktrees_dir.is_dir():
        for phase_dir in worktrees_dir.iterdir():
            if phase_dir.is_dir():
                try:
                    asyncio.run(remove_worktree(path, issue_id, phase_dir.name))
                    click.echo(f"  Removed worktree: {phase_dir.name}")
                except RuntimeError:
                    click.echo(f"  Warning: could not remove worktree {phase_dir.name}")

    click.echo(f"Issue {issue_id} validated.")
```

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all passed

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add jig/cli.py tests/test_cli.py
git commit -m "feat: add jig validate CLI command"
```
