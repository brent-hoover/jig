"""Agent runner -- spawns Claude Code agents via the SDK."""

import re
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, ResultMessage, SystemMessage

# Matches ANSI CSI escape sequences (e.g. "\x1b[31m") and standalone ESC chars.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]")
# Matches control characters except \t (we convert it to space anyway).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _sanitize_for_tui(text: str, limit: int = 120) -> str:
    """Make agent text safe for single-line rendering in the TUI.

    Strips ANSI escapes, collapses whitespace (newlines, tabs) to single
    spaces, removes other control characters, and truncates to ``limit``.
    """
    if not text:
        return ""
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text

from jig.store import MessageBus
from jig.events import EventEmitter, JigEvent
from jig.models import AgentTypeConfig, AgentInstance, Issue, ProjectContext
from jig.persistence import load_task


from jig.mcp_server import create_agent_mcp_server


def _tool_detail(tool_name: str, tool_input: dict) -> str:
    """Extract a short human-readable detail from a tool call."""
    if tool_name in ("Read", "Write", "Edit"):
        path = tool_input.get("file_path", "")
        # Show just the filename or last 2 path components
        parts = path.rsplit("/", 2)
        short = "/".join(parts[-2:]) if len(parts) > 1 else path
        return _sanitize_for_tui(short, limit=80)
    if tool_name == "Bash":
        return _sanitize_for_tui(tool_input.get("command", ""), limit=80)
    if tool_name == "Grep":
        return _sanitize_for_tui(tool_input.get("pattern", ""), limit=60)
    if tool_name == "Glob":
        return _sanitize_for_tui(tool_input.get("pattern", ""), limit=60)
    if tool_name == "Agent":
        return _sanitize_for_tui(tool_input.get("description", ""), limit=60)
    # Generic: show first string value
    for v in tool_input.values():
        if isinstance(v, str) and v:
            return _sanitize_for_tui(v, limit=60)
    return ""


def _build_project_section(ctx: ProjectContext) -> str:
    """Build the project context section for agent prompts."""
    if not ctx.name and not ctx.description:
        return ""
    lines = ["## Project Context\n"]
    if ctx.name:
        lines.append(f"- **Project**: {ctx.name}")
    if ctx.description:
        lines.append(f"- **Description**: {ctx.description}")
    if ctx.language:
        lines.append(f"- **Language**: {ctx.language}")
    if ctx.framework:
        lines.append(f"- **Framework**: {ctx.framework}")
    if ctx.package_manager:
        lines.append(f"- **Package manager**: {ctx.package_manager}")
    if ctx.setup_commands:
        lines.append(f"- **Setup commands**: `{'; '.join(ctx.setup_commands)}`")
    if ctx.build_command:
        lines.append(f"- **Build**: `{ctx.build_command}`")
    if ctx.test_command:
        lines.append(f"- **Test**: `{ctx.test_command}`")
    if ctx.docs:
        lines.append(f"- **Key docs**: {', '.join(ctx.docs)}")
    if ctx.notes:
        lines.append(f"\n{ctx.notes}")
    lines.append(
        f"\nIMPORTANT: Use the project's package manager ({ctx.package_manager or 'as appropriate'}) "
        f"and setup commands when initializing or adding dependencies. "
        f"Always create a .gitignore appropriate for {ctx.language or 'the project'} before committing."
    )
    return "\n".join(lines) + "\n\n"


def _build_issue_section(issue: Issue) -> str:
    """Build the issue context section for agent prompts."""
    lines = [f"## Issue: {issue.title}\n"]
    if issue.description:
        lines.append(issue.description)
    return "\n".join(lines) + "\n\n"


async def run_agent(
    project_path: Path,
    issue_id: str,
    task_id: str,
    agent_type: AgentTypeConfig,
    worktree_path: Path,
    max_turns: int = 50,
    emitter: EventEmitter | None = None,
    issue: Issue | None = None,
    project_context: ProjectContext | None = None,
    agent_instance: AgentInstance | None = None,
    bus: MessageBus | None = None,
) -> str:
    """Run a single agent on a task.

    Spawns a Claude Code agent via the SDK, configured with:
    - The agent type's system prompt and allowed tools
    - A Jig MCP server with publish_message, report_completion, request_context
    - The worktree as the working directory

    ``bus`` is the shared :class:`MessageBus` instance. Callers (e.g.
    :class:`Orchestrator`) should construct one bus per project run and
    thread it through so that cross-agent publish/subscribe actually
    reaches live subscribers. If ``bus`` is ``None`` a fresh one is
    created and loaded for standalone (non-orchestrated) invocations.

    Returns the agent's final result text.
    """
    if bus is None:
        bus = MessageBus(project_path / ".jig" / "store" / "messages.jsonl")
        await bus.load()
    task = load_task(project_path, issue_id, task_id)
    agent_id = agent_instance.id if agent_instance else f"{agent_type.role}-{task_id}"

    mcp_server = create_agent_mcp_server(
        bus=bus,
        project_path=project_path,
        issue_id=issue_id,
        task_id=task_id,
        agent_id=agent_id,
        worktree_path=worktree_path,
    )

    # Build prompt with project and issue context
    prompt_parts = []

    if project_context:
        prompt_parts.append(_build_project_section(project_context))

    if issue:
        prompt_parts.append(_build_issue_section(issue))

    # Inject agent memories if available
    if agent_instance and agent_instance.memory:
        memory_lines = "\n".join(f"- {m}" for m in agent_instance.memory)
        prompt_parts.append(
            f"## Your Memories (from previous sessions)\n\n{memory_lines}\n\n"
        )

    prompt_parts.append(
        f"## Task\n\n{task.description}\n\n"
        f"## Acceptance Criteria\n\n{task.acceptance_criteria}\n\n"
        f"## Instructions\n\n"
        f"You MUST only work within the current directory ({worktree_path}). "
        f"Do NOT read, write, or access any files outside this directory. "
        f"When you are done, call the "
        f"report_completion tool with status 'success' and a brief reason. "
        f"If you need more information, call report_completion with status "
        f"'needs_info' and explain what you need. If you are blocked, call "
        f"report_completion with status 'blocked' and explain the blocker."
    )

    prompt = "".join(prompt_parts)

    resume_id = agent_instance.session_id if agent_instance else None

    options = ClaudeAgentOptions(
        cwd=str(worktree_path),
        allowed_tools=agent_type.allowed_tools,
        disallowed_tools=[],
        system_prompt=agent_type.phase_prompt,
        mcp_servers={"jig": mcp_server},
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        **({"resume": resume_id} if resume_id else {}),
    )

    async def _emit(event_type: str, data: dict) -> None:
        if emitter:
            await emitter.emit(JigEvent(type=event_type, data=data))

    await _emit("agent_started", {
        "phase": task_id,
        "agent": agent_type.role,
    })

    captured_session_id = None
    result_text = ""
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "name"):
                    # ToolUseBlock — extract a short summary from input
                    tool_input = getattr(block, "input", {}) or {}
                    detail = _tool_detail(block.name, tool_input)
                    await _emit("agent_tool_use", {
                        "phase": task_id,
                        "tool": block.name,
                        "detail": detail,
                    })
                elif hasattr(block, "text") and block.text:
                    # TextBlock — send a sanitized single-line preview
                    preview = _sanitize_for_tui(block.text, limit=120)
                    if preview:
                        await _emit("agent_text", {
                            "phase": task_id,
                            "text": preview,
                        })
        elif isinstance(message, SystemMessage) and getattr(message, "subtype", None) == "init":
            captured_session_id = (message.data or {}).get("session_id")
        elif isinstance(message, ResultMessage):
            result_text = message.result or ""
            await _emit("agent_result", {
                "phase": task_id,
                "num_turns": getattr(message, "num_turns", None),
                "duration_ms": getattr(message, "duration_ms", None),
                "cost_usd": getattr(message, "total_cost_usd", None),
            })
        else:
            # Fallback for result-like messages (e.g. from mocks)
            result = getattr(message, "result", None)
            if isinstance(result, str):
                result_text = result

    if agent_instance and captured_session_id:
        agent_instance.session_id = captured_session_id

    return result_text
