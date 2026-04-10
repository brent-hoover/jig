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
MAX_PHASE_RETRIES = 3  # Consecutive failures/blocks on a single phase before giving up


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
            # Happy path: decide mechanically from persisted state. Only
            # consult the LLM when there's an actual judgment call — i.e.
            # a recent failed/blocked result that's still below the retry
            # cap and could plausibly be recovered by rerouting.
            decision = self._next_action(workflow)
            if decision is None:
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
                agent_type=phase_config.role,
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
            if phase.name == phase_name:
                return phase
        return None

    def _next_action(self, workflow: WorkflowConfig) -> dict | None:
        """Deterministically compute the next action from persisted history.

        Returns a decision dict for the mechanical cases:
        - no history → run the first declared phase
        - last result was ``needs_info`` → pause (human must resolve it)
        - last result was ``success`` → run the next pending phase, or
          return ``done`` when every declared phase has succeeded
        - same phase has failed/blocked ``MAX_PHASE_RETRIES`` times in
          a row → fail the workflow

        Returns ``None`` when the next step is a genuine judgment call
        (recent failure/block, below the retry cap) and the LLM should
        decide whether to retry, reroute to an earlier phase, or abort.
        """
        history = self._phase_history

        if not history:
            first = workflow.phases[0]
            return {
                "action": "run_phase",
                "phase": first.name,
                "reasoning": "Starting the workflow at the first declared phase.",
            }

        last = history[-1]
        last_result = last.get("result")
        last_phase = last.get("phase", "")
        last_reason = last.get("reason", "")

        if last_result == "needs_info":
            return {
                "action": "pause",
                "reasoning": last_reason or f"Phase '{last_phase}' needs information from the user.",
            }

        if last_result in ("failed", "blocked"):
            # Count consecutive failures/blocks of the same phase.
            consecutive = 0
            for entry in reversed(history):
                if entry.get("phase") != last_phase:
                    break
                if entry.get("result") in ("failed", "blocked"):
                    consecutive += 1
                else:
                    break
            if consecutive >= MAX_PHASE_RETRIES:
                return {
                    "action": "fail",
                    "reasoning": (
                        f"Phase '{last_phase}' {last_result} {consecutive} times in a row. "
                        f"Last reason: {last_reason or 'no reason provided'}"
                    ),
                }
            # Below the cap: the LLM gets to decide how to recover.
            return None

        # last_result is "success" (or an unknown mechanical state).
        # Run the first declared phase that has no success entry yet.
        successful = {h.get("phase") for h in history if h.get("result") == "success"}
        for phase in workflow.phases:
            if phase.name not in successful:
                return {
                    "action": "run_phase",
                    "phase": phase.name,
                    "reasoning": f"Advancing to next pending phase after {last_phase} succeeded.",
                }

        return {
            "action": "done",
            "reasoning": "Every declared phase has a successful entry in history.",
        }

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
        """Ask the orchestrator LLM how to recover from a phase failure.

        This method is only called when ``_next_action`` returns ``None``,
        which means the most recent phase result was ``failed`` or ``blocked``
        and we are still below the retry cap. The LLM's job is narrow:
        decide between retrying the same phase, routing back to an earlier
        phase to fix the underlying issue, or giving up.
        """
        last = self._phase_history[-1]
        last_phase = last.get("phase", "")
        last_reason = last.get("reason", "")

        # Render the per-phase status so the LLM can see what's already
        # succeeded and what's still pending.
        successful = {
            h["phase"] for h in self._phase_history if h.get("result") == "success"
        }
        phase_status_lines = []
        for p in workflow.phases:
            name = p.name
            if name in successful:
                marker = "DONE"
            else:
                entry = next(
                    (h for h in reversed(self._phase_history) if h.get("phase") == name),
                    None,
                )
                marker = (entry.get("result") if entry else "pending").upper()
            phase_status_lines.append(f"  - {name} ({p.role}): {marker}")
        phase_status = "\n".join(phase_status_lines)

        prompt = f"""You are the orchestrator for a software development workflow. A phase just failed and you need to decide how to recover.

## Issue
- **ID**: {issue.id}
- **Title**: {issue.title}
- **Description**: {issue.description or '(no description provided — use the title)'}

## Workflow Phases (declared order, with current status)
{phase_status}

## Phase History (chronological)
{json.dumps(self._phase_history, indent=2)}

## What just happened
Phase `{last_phase}` reported `{last.get("result")}`. Reason: {last_reason or "(no reason provided)"}

## Your Job
Decide how to recover. Respond with a JSON object (and nothing else) containing:

- `action`: one of "run_phase", "fail"
- `phase`: (required if action is "run_phase") the phase name to execute next
- `reasoning`: brief explanation
- `context_for_agent`: (optional) extra instructions for the agent — use this to tell the agent what needs to change

## Options
1. **Retry the same phase** (`action: "run_phase"`, `phase: "{last_phase}"`): pick this if the failure looks transient or the agent can fix it with clearer instructions. Use `context_for_agent` to describe what should change.
2. **Route back to an earlier phase**: pick this if the root cause is upstream (e.g. the spec is wrong, tests were malformed). Set `phase` to the earlier phase and explain in `context_for_agent` what needs to be fixed there.
3. **Fail the workflow** (`action: "fail"`): pick this if the problem cannot be resolved automatically.

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
        issue.current_phase = phase.name
        save_issue(self._project_path, issue)
        await self._emit("phase_started", {"phase": phase.name, "agent_type": phase.role})

        agent_type = load_agent_type(self._project_path, phase.role)

        # Clean up any existing worktree/branch from a prior run
        worktree_path = self._project_path / ".jig" / "worktrees" / self._issue_id / phase.name
        branch_name = f"jig/{self._issue_id}/{phase.name}"
        if worktree_path.exists():
            await self._emit("orchestrator_info", {"message": f"Cleaning up prior worktree for {phase.name}"})
            try:
                await remove_worktree(self._project_path, self._issue_id, phase.name)
            except RuntimeError:
                pass
        try:
            from jig.worktree import _run_git
            await _run_git(self._project_path, "branch", "-D", branch_name)
        except RuntimeError:
            pass

        # Create worktree
        await self._emit("orchestrator_info", {"message": f"Creating worktree for {phase.name} from {base_branch}"})
        worktree_path = await create_worktree(
            self._project_path,
            self._issue_id,
            phase.name,
            base_branch,
        )

        # Create task — include extra context from orchestrator if this is a retry
        description = f"Execute {phase.name} phase for issue: {issue.title}"
        if extra_context:
            description += f"\n\n## Additional Context from Orchestrator\n\n{extra_context}"

        task = Task(
            id=phase.name,
            description=description,
            acceptance_criteria=f"Complete the {phase.name} phase successfully",
            agent_type=phase.role,
        )
        save_task(self._project_path, self._issue_id, task)

        # Run agent
        await self._emit("orchestrator_info", {"message": f"Launching {phase.role} agent"})
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
        await self._emit("orchestrator_info", {"message": f"Committing {phase.name} changes"})
        await commit_worktree(worktree_path, f"{phase.name}: {issue.title}")
        await self._emit("phase_completed", {"phase": phase.name})

        # Check completion state
        task = load_task(self._project_path, self._issue_id, task.id)

        result = {
            "phase": phase.name,
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
