"""Orchestrator — an Opus-level reasoning agent that coordinates workflow phases."""

import json
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, ResultMessage

from jig.agent import run_agent
from jig.events import EventEmitter, JigEvent
from jig.models import (
    CompletionState,
    Issue,
    IssueStatus,
    PhaseConfig,
    ProjectContext,
    Task,
    WorkflowConfig,
)
from jig.persistence import (
    load_agent_type,
    load_issue,
    load_project_context,
    load_task,
    load_workflow,
    save_issue,
    save_task,
)
from jig.worktree import commit_worktree, create_worktree, merge_issue, remove_worktree


MAX_TOTAL_PHASES = 20  # Safety limit to prevent infinite loops


class Orchestrator:
    def __init__(
        self,
        project_path: Path,
        issue_id: str,
        workflow_name: str = "default",
        emitter: EventEmitter | None = None,
    ) -> None:
        self._project_path = project_path
        self._issue_id = issue_id
        self._workflow_name = workflow_name
        self._emitter = emitter
        self._phase_history: list[dict] = []
        self._last_branch: str | None = None

    async def _emit(self, event_type: str, data: dict | None = None) -> None:
        if self._emitter:
            await self._emitter.emit(JigEvent(type=event_type, data=data or {}))

    async def run(self) -> None:
        """Execute the workflow for the issue, using LLM reasoning for routing."""
        workflow = load_workflow(self._project_path, self._workflow_name)
        issue = load_issue(self._project_path, self._issue_id)
        self._project_context = load_project_context(self._project_path)
        self._last_branch = issue.base_branch

        issue.status = IssueStatus.IN_PROGRESS
        save_issue(self._project_path, issue)
        await self._emit("workflow_started", {"issue_id": self._issue_id, "workflow": self._workflow_name})

        phases_executed = 0

        while phases_executed < MAX_TOTAL_PHASES:
            # Ask the orchestrator LLM what to do next
            decision = await self._decide_next_phase(workflow, issue)
            await self._emit("orchestrator_decision", {
                "action": decision["action"],
                "phase": decision.get("phase"),
                "reasoning": decision.get("reasoning", ""),
            })

            if decision["action"] == "done":
                break

            if decision["action"] == "fail":
                issue.status = IssueStatus.FAILED
                save_issue(self._project_path, issue)
                await self._emit("workflow_failed", {
                    "issue_id": self._issue_id,
                    "reason": decision.get("reasoning", "Orchestrator decided to abort"),
                })
                raise OrchestratorFailed(decision.get("reasoning", "Orchestrator decided to abort"))

            if decision["action"] == "pause":
                issue.status = IssueStatus.PENDING
                save_issue(self._project_path, issue)
                raise OrchestratorPaused(decision.get("reasoning", "Orchestrator paused for user input"))

            # action == "run_phase"
            phase_name = decision["phase"]
            phase_config = self._find_phase(workflow, phase_name)
            if not phase_config:
                await self._emit("orchestrator_info", {"message": f"Unknown phase '{phase_name}', skipping"})
                self._phase_history.append({
                    "phase": phase_name,
                    "result": "error",
                    "reason": f"Phase '{phase_name}' not found in workflow",
                })
                continue

            # Determine base branch — use last successful branch or issue base
            base_branch = self._last_branch or issue.base_branch

            # Pass orchestrator context to the agent if provided
            extra_context = decision.get("context_for_agent", "")

            result = await self._execute_phase(phase_config, issue, base_branch, extra_context)
            self._phase_history.append(result)

            if result["result"] == "success":
                self._last_branch = result["branch"]

            phases_executed += 1

        # Merge results
        if self._last_branch and self._last_branch != issue.base_branch:
            last_phase = self._last_branch.split("/")[-1]  # extract phase from branch name
            strategy = self._project_context.merge_strategy
            await self._emit("orchestrator_info", {"message": f"Merging with strategy: {strategy.value}"})
            try:
                merge_result = await merge_issue(
                    self._project_path,
                    self._issue_id,
                    last_phase,
                    issue.base_branch,
                    strategy,
                )
                await self._emit("merge_completed", {
                    "issue_id": self._issue_id,
                    "strategy": strategy.value,
                    "result": merge_result,
                })
            except RuntimeError as e:
                await self._emit("merge_failed", {
                    "issue_id": self._issue_id,
                    "error": str(e),
                })

        issue.status = IssueStatus.COMPLETED
        save_issue(self._project_path, issue)
        await self._emit("workflow_completed", {"issue_id": self._issue_id})

    def _find_phase(self, workflow: WorkflowConfig, phase_name: str) -> PhaseConfig | None:
        """Find a phase config by name."""
        for phase in workflow.phases:
            if phase.name.value == phase_name:
                return phase
        return None

    async def _decide_next_phase(self, workflow: WorkflowConfig, issue: Issue) -> dict:
        """Ask the orchestrator LLM to decide what phase to run next."""
        phases_list = [
            {"name": p.name.value, "agent_type": p.agent_type}
            for p in workflow.phases
        ]

        prompt = f"""You are the orchestrator for a software development workflow.

## Issue
- **ID**: {issue.id}
- **Title**: {issue.title}
- **Description**: {issue.description or 'No description'}

## Available Phases
{json.dumps(phases_list, indent=2)}

## Phase History (what has been executed so far)
{json.dumps(self._phase_history, indent=2) if self._phase_history else "No phases executed yet."}

## Your Job
Decide what to do next. You MUST respond with a valid JSON object (and nothing else) with these fields:

- `action`: one of "run_phase", "done", "fail", "pause"
- `phase`: (required if action is "run_phase") the phase name to execute next
- `reasoning`: brief explanation of your decision
- `context_for_agent`: (optional) additional instructions or context to pass to the agent for this phase, especially if this is a retry due to a prior failure

## Rules
- Follow the workflow order (spec → test → implement → review → validate → document) for the initial pass
- If a phase failed, you may route back to an earlier phase to fix the issue. Include context_for_agent explaining what needs to be fixed
- If the same phase has failed 3+ times, consider failing the workflow
- When all phases have completed successfully, return action "done"
- If you need user input to proceed, return action "pause" with reasoning
- Be concise in reasoning

Respond with ONLY the JSON object, no markdown fences, no explanation outside the JSON."""

        await self._emit("orchestrator_info", {"message": "Deciding next phase..."})

        options = ClaudeAgentOptions(
            model="claude-opus-4-6",
            max_turns=1,
            system_prompt="You are a workflow orchestrator. Respond only with valid JSON.",
            permission_mode="bypassPermissions",
        )

        result_text = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text") and block.text:
                        result_text += block.text
            elif isinstance(message, ResultMessage):
                if message.result:
                    result_text = message.result
            else:
                result = getattr(message, "result", None)
                if isinstance(result, str):
                    result_text = result

        # Parse the decision
        try:
            # Strip markdown fences if present
            text = result_text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            decision = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # If LLM response isn't valid JSON, try to extract it
            await self._emit("orchestrator_info", {"message": f"Failed to parse decision, defaulting to fail"})
            decision = {
                "action": "fail",
                "reasoning": f"Could not parse orchestrator response: {result_text[:200]}",
            }

        return decision

    async def _execute_phase(self, phase: PhaseConfig, issue: Issue, base_branch: str, extra_context: str = "") -> dict:
        """Execute a single workflow phase. Returns a result dict for history."""
        issue.current_phase = phase.name.value
        save_issue(self._project_path, issue)
        await self._emit("phase_started", {"phase": phase.name.value, "agent_type": phase.agent_type})

        agent_type = load_agent_type(self._project_path, phase.agent_type)

        # Clean up any existing worktree/branch from a prior run
        worktree_path = self._project_path / ".jig" / "worktrees" / self._issue_id / phase.name.value
        branch_name = f"jig/{self._issue_id}/{phase.name.value}"
        if worktree_path.exists():
            await self._emit("orchestrator_info", {"message": f"Cleaning up prior worktree for {phase.name.value}"})
            try:
                await remove_worktree(self._project_path, self._issue_id, phase.name.value)
            except RuntimeError:
                pass
        try:
            from jig.worktree import _run_git
            await _run_git(self._project_path, "branch", "-D", branch_name)
        except RuntimeError:
            pass

        # Create worktree
        await self._emit("orchestrator_info", {"message": f"Creating worktree for {phase.name.value} from {base_branch}"})
        worktree_path = await create_worktree(
            self._project_path,
            self._issue_id,
            phase.name.value,
            base_branch,
        )

        # Create task — include extra context from orchestrator if this is a retry
        description = f"Execute {phase.name.value} phase for issue: {issue.title}"
        if extra_context:
            description += f"\n\n## Additional Context from Orchestrator\n\n{extra_context}"

        task = Task(
            id=phase.name.value,
            description=description,
            acceptance_criteria=f"Complete the {phase.name.value} phase successfully",
            agent_type=phase.agent_type,
        )
        save_task(self._project_path, self._issue_id, task)

        # Run agent
        await self._emit("orchestrator_info", {"message": f"Launching {phase.agent_type} agent"})
        await run_agent(
            project_path=self._project_path,
            issue_id=self._issue_id,
            task_id=task.id,
            agent_type=agent_type,
            worktree_path=worktree_path,
            emitter=self._emitter,
            issue=issue,
            project_context=self._project_context,
        )

        # Commit worktree changes
        await self._emit("orchestrator_info", {"message": f"Committing {phase.name.value} changes"})
        await commit_worktree(worktree_path, f"{phase.name.value}: {issue.title}")
        await self._emit("phase_completed", {"phase": phase.name.value})

        # Check completion state
        task = load_task(self._project_path, self._issue_id, task.id)

        result = {
            "phase": phase.name.value,
            "branch": branch_name,
            "completion_state": task.completion_state.value if task.completion_state else "unknown",
            "reason": task.completion_reason or "",
        }

        if task.completion_state == CompletionState.SUCCESS:
            result["result"] = "success"
        elif task.completion_state == CompletionState.NEEDS_INFO:
            result["result"] = "needs_info"
        elif task.completion_state in (CompletionState.BLOCKED, CompletionState.FAILED):
            result["result"] = "failed"
        else:
            # No explicit completion state — assume success (agent may not have called report_completion)
            result["result"] = "success"

        return result


class OrchestratorPaused(Exception):
    """Raised when the orchestrator pauses for user input."""


class OrchestratorFailed(Exception):
    """Raised when the orchestrator encounters a failure."""
