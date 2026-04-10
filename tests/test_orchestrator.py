import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from jig.models import (
    AgentStatus,
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
    list_agent_instances,
    load_issue,
    load_task,
    save_agent_type,
    save_issue,
    save_task,
    save_workflow,
)
from jig.store import Database


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
    for d in ("issues", "agent_types", "workflows", "worktrees", "agents"):
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

    @patch("jig.orchestrator.run_agent")
    async def test_passes_shared_bus_to_run_agent(self, mock_run_agent, git_project: Path):
        """Orchestrator constructs a single MessageBus and threads it
        through to every run_agent invocation. Regression: previously
        run_agent built its own private bus, breaking cross-agent
        publish/subscribe fan-out."""
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        mock_run_agent.assert_called_once()
        call_kwargs = mock_run_agent.call_args.kwargs
        assert "bus" in call_kwargs
        assert call_kwargs["bus"] is orchestrator._bus

    @patch("jig.orchestrator.run_agent")
    async def test_constructs_bus_monitor_with_shared_bus(self, mock_run_agent, git_project: Path):
        """Orchestrator builds a BusMonitor wired to its shared bus and
        issue id so dormant-agent wake-ups can fire during a run."""
        mock_run_agent.return_value = "Done"

        recorded: dict = {}

        class StubMonitor:
            def __init__(self, project_path, bus, issue_id, on_wake):
                recorded["project_path"] = project_path
                recorded["bus"] = bus
                recorded["issue_id"] = issue_id
                recorded["on_wake"] = on_wake

            async def start(self):
                # Idle forever until cancelled so the orchestrator's
                # finally block exercises the stop/cancel path.
                import asyncio
                await asyncio.Event().wait()

            def stop(self):
                pass

        with patch("jig.orchestrator.BusMonitor", StubMonitor):
            orchestrator = Orchestrator(git_project, "issue-1")
            await orchestrator.run()

        assert recorded["bus"] is orchestrator._bus
        assert recorded["issue_id"] == "issue-1"
        assert recorded["project_path"] == git_project
        assert callable(recorded["on_wake"])


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
    for d in ("issues", "agent_types", "workflows", "worktrees", "agents"):
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

        db = Database(git_project_full_workflow / ".jig" / "store")
        phase_history = await db.collection(
            "phase_history",
            index_fields=["issue_id"],
            model=PhaseHistoryEntry,
        )
        await phase_history.insert(
            PhaseHistoryEntry(
                issue_id="issue-1",
                phase="spec",
                agent_type="spec",
                result="success",
                branch="jig/issue-1/spec",
                reason="Design doc written",
            )
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


class TestOrchestratorPoolIntegration:
    @patch("jig.orchestrator.run_agent")
    async def test_acquires_agent_from_pool(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        # run_agent should have been called with an agent_instance
        call_kwargs = mock_run_agent.call_args.kwargs
        assert "agent_instance" in call_kwargs
        instance = call_kwargs["agent_instance"]
        assert hasattr(instance, "id")
        assert hasattr(instance, "agent_type")

    @patch("jig.orchestrator.run_agent")
    async def test_releases_agent_after_phase(self, mock_run_agent, git_project: Path):
        mock_run_agent.return_value = "Done"

        orchestrator = Orchestrator(git_project, "issue-1")
        await orchestrator.run()

        # After completion, agent should be dormant
        instances = list_agent_instances(git_project, agent_type="spec")
        assert len(instances) >= 1
        # At least one should be dormant (released)
        dormant = [i for i in instances if i.status == AgentStatus.DORMANT]
        assert len(dormant) >= 1


class TestOrchestratorAgentPersistence:
    @patch("jig.orchestrator.run_agent")
    @patch("jig.orchestrator.query")
    async def test_orchestrator_creates_agent_instance(self, mock_orch_query, mock_run_agent, git_project_full_workflow: Path):
        """When the orchestrator makes a recovery decision, it should create/use a persistent instance."""
        call_count = 0

        async def fake_run_agent(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First phase fails
                task = load_task(kwargs["project_path"], kwargs["issue_id"], kwargs["task_id"])
                task.completion_state = CompletionState.FAILED
                task.completion_reason = "Compile error"
                save_task(kwargs["project_path"], kwargs["issue_id"], task)
            return "Done"

        mock_run_agent.side_effect = fake_run_agent

        # Mock the orchestrator LLM query to retry the phase
        async def fake_orch_query(*args, **kwargs):
            mock_result = MagicMock()
            mock_result.result = '{"action": "run_phase", "phase": "spec", "reasoning": "Retrying with fixes"}'
            yield mock_result

        mock_orch_query.side_effect = fake_orch_query

        orchestrator = Orchestrator(git_project_full_workflow, "issue-1")
        await orchestrator.run()

        # Orchestrator agent instance should exist with memory
        orch_instances = list_agent_instances(git_project_full_workflow, agent_type="orchestrator")
        assert len(orch_instances) >= 1
        # Should have recorded what it decided
        orch = orch_instances[0]
        assert len(orch.memory) >= 1
        assert "Retrying" in orch.memory[0] or "failed" in orch.memory[0]
