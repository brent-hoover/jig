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
    PhaseHistoryEntry,
    ProjectContext,
    Task,
    WorkflowConfig,
)
from jig.persistence import (
    append_phase_history,
    load_agent_type,
    load_issue,
    load_phase_history,
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

        # Load persisted phase history so resumed runs see what previous
        # invocations already accomplished. Set _last_branch to the most
        # recent successful phase's branch (if any) so worktrees chain.
        persisted = load_phase_history(self._project_path, self._issue_id)
        self._phase_history = [self._entry_to_dict(e) for e in persisted]
        self._last_branch = issue.base_branch
        for entry in persisted:
            if entry.result == "success" and entry.branch:
                self._last_branch = entry.branch

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
                self._record_history(PhaseHistoryEntry(
                    phase=phase_name,
                    agent_type="",
                    result="error",
                    reason=f"Phase '{phase_name}' not found in workflow",
                ))
                continue

            # Determine base branch — use last successful branch or issue base
            base_branch = self._last_branch or issue.base_branch

            # Pass orchestrator context to the agent if provided
            extra_context = decision.get("context_for_agent", "")

            result = await self._execute_phase(phase_config, issue, base_branch, extra_context)
            self._record_history(PhaseHistoryEntry(
                phase=result["phase"],
                agent_type=phase_config.agent_type,
                result=result["result"],
                branch=result["branch"],
                reason=result.get("reason", ""),
            ))

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

    @staticmethod
    def _entry_to_dict(entry: PhaseHistoryEntry) -> dict:
        """Project a PhaseHistoryEntry to the dict shape used by the LLM prompt."""
        return {
            "phase": entry.phase,
            "agent_type": entry.agent_type,
            "result": entry.result,
            "branch": entry.branch,
            "reason": entry.reason,
        }

    def _record_history(self, entry: PhaseHistoryEntry) -> None:
        """Append a phase result to in-memory history and durable storage."""
        self._phase_history.append(self._entry_to_dict(entry))
        append_phase_history(self._project_path, self._issue_id, entry)

    async def _decide_next_phase(self, workflow: WorkflowConfig, issue: Issue) -> dict:
        """Ask the orchestrator LLM to decide what phase to run next."""
        phases_list = [
            {"name": p.name.value, "agent_type": p.agent_type}
            for p in workflow.phases
        ]

        # Build a status view: which phases are already successful, which
        # have failed/needed-info, which are untouched. This is what keeps
        # resume deterministic — the LLM sees exactly what's done.
        successful = {
            h["phase"] for h in self._phase_history if h.get("result") == "success"
        }
        phase_status_lines = []
        for p in workflow.phases:
            name = p.name.value
            if name in successful:
                marker = "DONE"
            else:
                last = next(
                    (h for h in reversed(self._phase_history) if h.get("phase") == name),
                    None,
                )
                marker = (last.get("result") if last else "pending").upper()
            phase_status_lines.append(f"  - {name} ({p.agent_type}): {marker}")
        phase_status = "\n".join(phase_status_lines)

        prompt = f"""You are the orchestrator for a software development workflow.

## Issue
- **ID**: {issue.id}
- **Title**: {issue.title}
- **Description**: {issue.description or '(no description provided — proceed using the title)'}

## Workflow Phases (declared order, with current status)
{phase_status}

## Phase History (full record of attempts in chronological order)
{json.dumps(self._phase_history, indent=2) if self._phase_history else "No phases executed yet."}

## Your Job
Decide what to do next. You MUST respond with a valid JSON object (and nothing else) with these fields:

- `action`: one of "run_phase", "done", "fail", "pause"
- `phase`: (required if action is "run_phase") the phase name to execute next
- `reasoning`: brief explanation of your decision
- `context_for_agent`: (optional) extra instructions for the agent, especially for retries after failure

## Hard rules (these are not negotiable)
1. **Never pick a phase whose status is DONE.** Those phases are already complete; re-running them wastes work and corrupts state.
2. **If the most recent entry in Phase History has `result: needs_info`, return action `"pause"`** with that entry's reason. `needs_info` is an agent telling you it needs input from the human user — you cannot route around it; only the user can resolve it.
3. **Never pause on your own initiative for a missing issue description.** If the description is empty, use the title and proceed. Pausing is only valid for rule 2.
4. **Do not return `"done"` while any phase still has status PENDING.** Every declared phase must reach DONE status before the workflow is complete. "validate" is not the end of the pipeline — check the declared list above. If you see pending phases, pick one.
5. If a phase has the same failure 3+ times in history, return action `"fail"` with the reason.

## Routing guidance (use judgment within these defaults)
- The declared workflow order is the default forward path. On a happy initial pass, walk it in order until every phase is DONE.
- After a `failed` or `blocked` result you may route back to an earlier phase to fix the underlying issue — include `context_for_agent` describing what needs to change.
- Skipping a phase is only acceptable if you have a concrete, specific reason the issue does not need it. Default to running every declared phase.

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
