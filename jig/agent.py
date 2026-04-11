"""Agent runner — spawns Claude Code agents via the SDK in streaming input mode."""

import asyncio
import logging
import re
from dataclasses import dataclass

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import ResultMessage

from jig.environment import load_environment_md
from jig.mcp_server import create_agent_mcp_server
from jig.prompt_builder import build_initial_prompt
from jig.runtime import AgentSpawnContext
from jig.skill_loader import load_all_skills, match_skills
from jig.store import Message
from jig.ticket import TicketStatus

_logger = logging.getLogger(__name__)

# Matches ANSI CSI escape sequences (e.g. "\x1b[31m") and standalone ESC chars.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]")
# Matches control characters except \t (we convert it to space anyway).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _sanitize_for_tui(text: str, limit: int = 120) -> str:
    """Make agent text safe for single-line rendering in the TUI."""
    if not text:
        return ""
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _tool_detail(tool_name: str, tool_input: dict) -> str:
    """Extract a short human-readable detail from a tool call."""
    if tool_name in ("Read", "Write", "Edit"):
        path = tool_input.get("file_path", "")
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
    for v in tool_input.values():
        if isinstance(v, str) and v:
            return _sanitize_for_tui(v, limit=60)
    return ""


@dataclass
class RunAgentResult:
    status: str  # "success" | "failed" | "blocked" | "needs_info"
    final_text: str


async def run_agent(ctx: AgentSpawnContext) -> RunAgentResult:
    """Run a Claude agent against a ticket in streaming input mode.

    Builds the initial prompt, subscribes to the ticket's bus topic, yields
    incoming bus events addressed to this role (or broadcast) as user turns,
    and terminates when the primary ticket reaches a terminal status
    (resolved, blocked, needs_info).

    Shutdown is cooperative: the function returns only when either the SDK
    emits a ResultMessage or a terminal status update arrives on the bus (via
    a ticket_updated payload or by the timeout-poll noticing a terminal status
    on the ticket record).
    """
    skills = match_skills(project=ctx.project, skills=load_all_skills())
    env_md = load_environment_md(ctx.project.path_or_default())
    memories = [
        learning.content for learning in await ctx.memory.get_role_learnings(ctx.role)
    ]
    comments = (
        await ctx.comments.for_ticket(ctx.parent.id) if ctx.parent else []
    )

    initial_prompt = build_initial_prompt(
        role_cfg=ctx.role_cfg,
        spawn_reason=ctx.spawn_reason,
        ticket=ctx.ticket,
        parent=ctx.parent,
        comments=comments,
        memories=memories,
        project=ctx.project,
        skills=skills,
        environment_md=env_md,
    )

    mcp_server = create_agent_mcp_server(
        tickets=ctx.tickets,
        comments=ctx.comments,
        memory=ctx.memory,
        bus=ctx.bus,
        agent_role=ctx.role,
        agent_cfg=ctx.role_cfg,
        worktree_path=ctx.worktree_path,
    )

    options = ClaudeAgentOptions(
        cwd=str(ctx.worktree_path),
        allowed_tools=ctx.role_cfg.allowed_tools,
        disallowed_tools=[],
        system_prompt=ctx.role_cfg.phase_prompt,
        mcp_servers={"jig": mcp_server},
        permission_mode="bypassPermissions",
    )

    topic = f"tickets.{ctx.ticket.id}"
    bus_queue = await ctx.bus.subscribe_agent(
        topic=topic,
        agent_id=f"{ctx.role}:{ctx.ticket.id}",
    )
    terminal_statuses = {
        TicketStatus.RESOLVED,
        TicketStatus.BLOCKED,
        TicketStatus.NEEDS_INFO,
    }
    terminal_values = {status.value for status in terminal_statuses}
    done = asyncio.Event()

    async def _prompt_stream():
        yield initial_prompt
        while not done.is_set():
            try:
                msg = await asyncio.wait_for(bus_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                current = await ctx.tickets.get(ctx.ticket.id)
                if current and current.status in terminal_statuses:
                    done.set()
                continue
            if not _is_relevant(msg, ctx):
                continue
            yield _format_bus_event(msg)
            payload = msg.payload or {}
            if (
                payload.get("kind") == "ticket_updated"
                and payload.get("ticket_id") == ctx.ticket.id
                and payload.get("status") in terminal_values
            ):
                done.set()

    final_text = ""
    try:
        async for message in query(prompt=_prompt_stream(), options=options):
            if isinstance(message, ResultMessage):
                final_text = message.result or ""
            else:
                # Fallback for mocked result-like messages
                result = getattr(message, "result", None)
                if isinstance(result, str):
                    final_text = result
    finally:
        done.set()
        # Remove the queue from the topic fan-out list to prevent a slow leak
        # in the orchestrator's per-ticket lifecycle.
        # NOTE: _agent_subscriptions in bus.py still retains the key; cleaning
        # that up is a known limitation to address in bus.py cleanup.
        try:
            await ctx.bus.unsubscribe(topic, bus_queue)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Failed to unsubscribe bus queue for %s: %s", topic, exc)

    current = await ctx.tickets.get(ctx.ticket.id)
    status = "success"
    if current is not None:
        status = _status_to_result(current.status)
    return RunAgentResult(status=status, final_text=final_text)


def _is_relevant(msg: Message, ctx: AgentSpawnContext) -> bool:
    return msg.to in (ctx.role, "broadcast") and msg.sender != ctx.role


def _format_bus_event(msg: Message) -> str:
    payload = msg.payload or {}
    kind = payload.get("kind", "event")
    if kind == "comment_posted":
        return (
            f"[comment from {payload.get('author')} on ticket "
            f"{payload.get('ticket_id')}]: {payload.get('content')}"
        )
    if kind == "ticket_created":
        return f"[new ticket {payload.get('ticket_id')} assigned to you]"
    return f"[{kind}] {payload}"


def _status_to_result(status: TicketStatus) -> str:
    if status == TicketStatus.RESOLVED:
        return "success"
    if status == TicketStatus.BLOCKED:
        return "blocked"
    if status == TicketStatus.NEEDS_INFO:
        return "needs_info"
    if status == TicketStatus.FAILED:
        return "failed"
    return "success"  # still in progress — treat as success for now
