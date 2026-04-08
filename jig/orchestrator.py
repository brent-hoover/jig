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
from jig.worktree import commit_worktree, create_worktree, remove_worktree


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

        # Clean up any existing worktree from a prior incomplete run
        worktree_path = self._project_path / ".jig" / "worktrees" / self._issue_id / phase.name.value
        if worktree_path.exists():
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
