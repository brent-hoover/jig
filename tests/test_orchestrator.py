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
    PhaseHistoryEntry,
    ProjectConfig,
    Task,
    WorkflowConfig,
)
from jig.orchestrator import Orchestrator, OrchestratorPaused, OrchestratorFailed
from jig.persistence import (
    append_phase_history,
    load_issue,
    load_phase_history,
    load_task,
    save_agent_type,
    save_issue,
    save_task,
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
        role="spec", system_prompt="Spec agent.", allowed_tools=["Read"],
    ))

    # Add single-phase workflow
    save_workflow(repo, WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
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
        assert call_kwargs["agent_type"].role == "spec"

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

    config = ProjectConfig(repo_path=str(repo))
    (jig_dir / "config.yaml").write_text(yaml.dump(config.model_dump(), default_flow_style=False))

    for role_name in ("spec", "test", "dev", "review", "validate", "document"):
        save_agent_type(repo, AgentTypeConfig(
            role=role_name, system_prompt=f"{role_name} agent.", allowed_tools=["Read"],
        ))

    save_workflow(repo, WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(name="spec", role="spec"),
            PhaseConfig(name="test", role="test"),
            PhaseConfig(name="implement", role="dev"),
            PhaseConfig(name="review", role="review"),
            PhaseConfig(name="validate", role="validate"),
            PhaseConfig(name="document", role="document"),
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

        assert mock_run_agent.call_count == 6
        agent_types = [
            call.kwargs["agent_type"].role
            for call in mock_run_agent.call_args_list
        ]
        assert agent_types == ["spec", "test", "dev", "review", "validate", "document"]

    @patch("jig.orchestrator.run_agent")
    async def test_issue_completed_after_all_phases(self, mock_run_agent, git_project_full_workflow: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        await orchestrator.run()

        issue = load_issue(git_project_full_workflow, "issue-1")
        assert issue.status == IssueStatus.COMPLETED
        assert issue.current_phase == "document"


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


class TestOrchestratorResume:
    @patch("jig.orchestrator.run_agent")
    async def test_resumes_from_last_completed_phase(self, mock_run_agent, git_project_full_workflow: Path):
        """If spec phase was completed, resume from test phase."""
        mock_run_agent.return_value = "Done"

        # Simulate spec phase already completed: create the spec branch
        # the prior run would have produced and persist a success entry.
        subprocess.run(
            ["git", "branch", "jig/issue-1/spec"],
            cwd=git_project_full_workflow,
            check=True,
            capture_output=True,
        )

        issue = load_issue(git_project_full_workflow, "issue-1")
        issue.current_phase = "spec"
        issue.status = IssueStatus.IN_PROGRESS
        save_issue(git_project_full_workflow, issue)

        append_phase_history(
            git_project_full_workflow,
            "issue-1",
            PhaseHistoryEntry(
                phase="spec",
                agent_type="spec",
                result="success",
                branch="jig/issue-1/spec",
                reason="Design doc written",
            ),
        )

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        await orchestrator.run()

        # Should only run 5 remaining phases (test, implement, review, validate, document)
        assert mock_run_agent.call_count == 5
        agent_types = [
            call.kwargs["agent_type"].role
            for call in mock_run_agent.call_args_list
        ]
        assert agent_types == ["test", "dev", "review", "validate", "document"]

    @patch("jig.orchestrator.run_agent")
    async def test_resumes_incomplete_phase(self, mock_run_agent, git_project_full_workflow: Path):
        """If spec phase started but task not completed, re-run it."""
        mock_run_agent.return_value = "Done"

        # Simulate spec phase started but no task completion
        issue = load_issue(git_project_full_workflow, "issue-1")
        issue.current_phase = "spec"
        issue.status = IssueStatus.IN_PROGRESS
        save_issue(git_project_full_workflow, issue)

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        await orchestrator.run()

        # Should run all 6 phases (spec re-run + test + implement + review + validate + document)
        assert mock_run_agent.call_count == 6


from jig.events import EventEmitter, JigEvent


class TestOrchestratorEvents:
    @patch("jig.orchestrator.run_agent")
    async def test_emits_phase_events(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Done"
        emitter = EventEmitter()
        queue = emitter.subscribe()

        orchestrator = Orchestrator(git_project, "issue-1", emitter=emitter)
        await orchestrator.run()

        events = []
        while not queue.empty():
            events.append(await queue.get())

        types = [e.type for e in events]
        assert "workflow_started" in types
        assert "phase_started" in types
        assert "phase_completed" in types
        assert "workflow_completed" in types
