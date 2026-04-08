import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from jig.models import (
    AgentTypeConfig,
    CompletionState,
    Issue,
    IssueStatus,
    PhaseConfig,
    ProjectConfig,
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

    config = ProjectConfig(repo_path=str(repo))
    (jig_dir / "config.yaml").write_text(yaml.dump(config.model_dump(), default_flow_style=False))

    # Add agent type
    save_agent_type(repo, AgentTypeConfig(
        name="spec", system_prompt="Spec agent.", allowed_tools=["Read"],
    ))

    # Add single-phase workflow
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
    async def test_updates_issue_status_to_completed(self, mock_run_agent, git_project: Path):
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

        issue = load_issue(git_project, "issue-1")
        assert issue.current_phase == "spec"

    @patch("jig.orchestrator.run_agent")
    async def test_creates_task_for_phase(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        task = load_task(git_project, "issue-1", "spec")
        assert task.agent_type == "spec"
