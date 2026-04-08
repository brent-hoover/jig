"""Agent runner -- spawns Claude Code agents via the SDK."""

from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions

from jig.bus import MessageBus
from jig.mcp_tools import create_jig_mcp_server
from jig.models import AgentTypeConfig
from jig.persistence import load_task


async def run_agent(
    project_path: Path,
    issue_id: str,
    task_id: str,
    agent_type: AgentTypeConfig,
    worktree_path: Path,
    max_turns: int = 50,
) -> str:
    """Run a single agent on a task.

    Spawns a Claude Code agent via the SDK, configured with:
    - The agent type's system prompt and allowed tools
    - A Jig MCP server with publish_message, report_completion, request_context
    - The worktree as the working directory

    Returns the agent's final result text.
    """
    bus = MessageBus(project_path)
    task = load_task(project_path, issue_id, task_id)
    agent_name = f"{agent_type.name}-{task_id}"

    mcp_server = create_jig_mcp_server(
        bus=bus,
        project_path=project_path,
        issue_id=issue_id,
        task_id=task_id,
        agent_name=agent_name,
        worktree_path=worktree_path,
    )

    prompt = (
        f"## Task\n\n{task.description}\n\n"
        f"## Acceptance Criteria\n\n{task.acceptance_criteria}\n\n"
        f"## Instructions\n\n"
        f"Work in the current directory. When you are done, call the "
        f"report_completion tool with status 'success' and a brief reason. "
        f"If you need more information, call report_completion with status "
        f"'needs_info' and explain what you need. If you are blocked, call "
        f"report_completion with status 'blocked' and explain the blocker."
    )

    options = ClaudeAgentOptions(
        cwd=str(worktree_path),
        allowed_tools=agent_type.allowed_tools,
        disallowed_tools=agent_type.denied_tools,
        system_prompt=agent_type.system_prompt,
        mcp_servers={"jig": mcp_server},
        permission_mode="bypassPermissions",
        max_turns=max_turns,
    )

    result_text = ""
    async for message in query(prompt=prompt, options=options):
        result = getattr(message, "result", None)
        if isinstance(result, str):
            result_text = result

    return result_text
