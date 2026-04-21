"""Agent runner — spawns Claude Code agents via the SDK in streaming input mode."""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
)

from jig.capability_compiler import compile as compile_capabilities
from jig.capability_compiler import materialize as materialize_capabilities
from jig.context_resolver import resolve_context_uris
from jig.environment import load_environment_md
from jig.events import EventEmitter, JigEvent
from jig.mcp_server import create_agent_mcp_server
from jig.persistence import list_roles
from jig.prompt_builder import build_initial_prompt
from jig.runtime import AgentSpawnContext
from jig.sandbox import BwrapConfig, BwrapTransport, sandbox_available
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


async def build_agent_prompt(ctx: AgentSpawnContext) -> str:
    """Assemble the full initial prompt an agent would receive for a ticket."""
    skills = match_skills(project=ctx.project, skills=load_all_skills())
    env_md = load_environment_md(ctx.project.path_or_default())
    memories = [
        learning.content for learning in await ctx.memory.get_role_learnings(ctx.role)
    ]
    entries = await ctx.threads.for_ticket(ctx.ticket.id)
    if ctx.parent:
        entries = await ctx.threads.for_ticket(ctx.parent.id) + entries

    project_path = ctx.project.path_or_default()
    # Required context — raise MissingContextError if any URI can't
    # resolve. Caller turns that into a ticket_failed event.
    required_context = await resolve_context_uris(
        ctx.role_cfg.required_context,
        ticket=ctx.ticket,
        parent=ctx.parent,
        threads=ctx.threads,
        worktree_path=ctx.worktree_path,
        project_path=project_path,
        strict=True,
    )
    optional_context = await resolve_context_uris(
        ctx.role_cfg.default_context,
        ticket=ctx.ticket,
        parent=ctx.parent,
        threads=ctx.threads,
        worktree_path=ctx.worktree_path,
        project_path=project_path,
    )
    # Both already wrap themselves in a "## Context" header when non-
    # empty; concat as-is. If both are non-empty they render as two
    # separate sections, which is fine — the reader can tell them apart
    # by the required URIs appearing first.
    resolved_context = required_context + optional_context

    all_roles = list_roles(ctx.project.path_or_default())

    return build_initial_prompt(
        role_cfg=ctx.role_cfg,
        spawn_reason=ctx.spawn_reason,
        ticket=ctx.ticket,
        parent=ctx.parent,
        entries=entries,
        memories=memories,
        project=ctx.project,
        skills=skills,
        environment_md=env_md,
        resolved_context=resolved_context,
        all_roles=all_roles,
        worktree_path=str(ctx.worktree_path),
        phase=ctx.phase,
    )


def _hook_bin_dir() -> Path:
    """Return the host directory containing the capability-enforcement
    hook scripts (``check-bash``, ``check-write``, ``check-path``).

    The ``jig.bin`` subpackage exists solely to host these files; its
    ``hook_bin_dir()`` resolves to the on-disk location regardless of
    whether jig is running from a source checkout, an editable
    install, or a wheel in site-packages. ``sandbox.py`` bind-mounts
    the returned path read-only at ``/jig/bin/`` inside the agent
    sandbox."""

    # Local import: keeps the module-load cost off paths that never
    # touch sandbox spawning (e.g. CLI commands that only read stores).
    from jig.bin import hook_bin_dir

    return hook_bin_dir()


def _materialize_capability_policy(ctx: AgentSpawnContext) -> Path | None:
    """Compile + write capability artefacts for a spawn.

    Returns the policy directory that was written (suitable for
    bind-mounting at ``/jig/policy/``), or ``None`` when the spawn has
    no declared capabilities / materialisation failed. Callers use
    the return value to decide whether to pass the directory into
    :class:`BwrapConfig` — a ``None`` result means no hook scripts
    were registered, so the sandbox doesn't need the policy mount.

    No-op when neither the role nor the phase declares capabilities —
    we don't want to silently clobber a hand-maintained
    ``.claude/settings.json`` in the worktree, and writing an empty
    ``rules.json`` with no hooks registered gains nothing at enforcement.

    Location:

    * ``rules.json`` → ``<project>/.jig/runtime/<ticket_id>/policy/``
      (outside the worktree so the git tree the agent sees stays clean;
      the sandbox bind-mounts this to ``/jig/policy/rules.json``).
    * ``.claude/settings.json`` → written inside the worktree because
      Claude Code discovers settings relative to its cwd.

    Failures are logged and non-fatal — hook policy is belt-and-braces
    on top of bwrap, so a materialisation glitch shouldn't block a
    spawn that already has Docker + bwrap isolation. Task G will make
    this fail-loud once the hooks are production-required.
    """
    role_caps = ctx.role_cfg.capabilities
    phase_caps = ctx.phase.capability_overrides if ctx.phase else None
    if role_caps is None and phase_caps is None:
        return None

    try:
        rules = compile_capabilities(role_caps, phase_caps)
        policy_dir = (
            ctx.project.path_or_default()
            / ".jig"
            / "runtime"
            / ctx.ticket.id
            / "policy"
        )
        rules_path, settings_path = materialize_capabilities(
            rules,
            worktree_path=ctx.worktree_path,
            policy_dir=policy_dir,
        )
        _logger.info(
            "capability policy materialised for %s on %s: rules=%s settings=%s",
            ctx.role,
            ctx.ticket.id,
            rules_path,
            settings_path,
        )
        return policy_dir
    except OSError:
        # Narrow: only swallow filesystem errors (disk full, perms,
        # broken mount). Bugs in compile/materialize should propagate
        # so the spawn fails loud rather than silently skipping
        # enforcement. Task G will tighten this further once hooks
        # are the primary enforcement layer.
        _logger.exception(
            "capability materialisation failed for %s on %s",
            ctx.role,
            ctx.ticket.id,
        )
        return None


def _resolve_external_mcps(allowed_mcps: list[str]) -> dict:
    """Resolve allowed MCP names to stdio server configs.

    Searches two locations for each name:
    1. User-level ``~/.claude/.mcp.json`` (``mcpServers`` key)
    2. Installed plugin cache (``~/.claude/plugins/cache/*/*/.mcp.json``)

    Returns a dict of ``{server_name: McpStdioServerConfig}`` suitable for
    merging into the ``mcp_servers`` option.
    """
    if not allowed_mcps:
        return {}

    import json
    from pathlib import Path

    result: dict = {}
    remaining = set(allowed_mcps)

    # 1. User-level .mcp.json
    user_mcp = Path.home() / ".claude" / ".mcp.json"
    if user_mcp.exists():
        try:
            servers = json.loads(user_mcp.read_text()).get("mcpServers", {})
            for name in list(remaining):
                if name in servers:
                    result[name] = servers[name]
                    remaining.discard(name)
        except (json.JSONDecodeError, OSError):
            _logger.warning("Failed to parse %s", user_mcp)

    if not remaining:
        return result

    # 2. Plugin cache — each plugin dir has .mcp.json with server configs
    plugins_cache = Path.home() / ".claude" / "plugins" / "cache"
    if plugins_cache.is_dir():
        for marketplace_dir in plugins_cache.iterdir():
            if not marketplace_dir.is_dir():
                continue
            for name in list(remaining):
                plugin_dir = marketplace_dir / name
                if not plugin_dir.is_dir():
                    continue
                # Find any version subdirectory containing .mcp.json
                for version_dir in plugin_dir.iterdir():
                    if not version_dir.is_dir():
                        continue
                    mcp_json = version_dir / ".mcp.json"
                    if not mcp_json.exists():
                        continue
                    try:
                        servers = json.loads(mcp_json.read_text())
                        for server_name, config in servers.items():
                            result[server_name] = config
                        remaining.discard(name)
                    except (json.JSONDecodeError, OSError):
                        _logger.warning("Failed to parse %s", mcp_json)
                    break

    for name in remaining:
        _logger.warning(
            "MCP '%s' not found in user settings or installed plugins", name
        )

    return result


async def run_agent(
    ctx: AgentSpawnContext, emitter: EventEmitter | None = None
) -> RunAgentResult:
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
    initial_prompt = await build_agent_prompt(ctx)
    _logger.info("prompt built for %s (%d chars)", ctx.role, len(initial_prompt))
    _logger.debug("--- SYSTEM PROMPT [%s] ---\n%s", ctx.role, ctx.role_cfg.phase_prompt)
    _logger.debug("--- INITIAL PROMPT [%s] ---\n%s", ctx.role, initial_prompt)

    all_roles = list_roles(ctx.project.path_or_default())
    mcp_server = create_agent_mcp_server(
        tickets=ctx.tickets,
        threads=ctx.threads,
        memory=ctx.memory,
        bus=ctx.bus,
        agent_role=ctx.role,
        agent_cfg=ctx.role_cfg,
        worktree_path=ctx.worktree_path,
        project_path=ctx.project.path_or_default(),
        valid_roles=frozenset(r.role for r in all_roles),
        package_manager=ctx.project.package_manager,
        checkpoints=ctx.checkpoints,
        phase_name=ctx.phase.name if ctx.phase else "",
    )

    mcp_servers: dict = {"jig": mcp_server}
    external = _resolve_external_mcps(ctx.role_cfg.allowed_mcps)
    mcp_servers.update(external)
    if external:
        _logger.info("external MCPs for %s: %s", ctx.role, list(external.keys()))

    # Phase 5 Task F: compile capability policy (role base + phase
    # override) and materialize the two enforcement artefacts before
    # Claude Code starts. Only writes files when a declaration actually
    # exists — a spawn with no declared policy gets no .claude/settings
    # overwrite and no rules.json clutter. Task G's hook scripts read
    # rules.json at tool-eval time.
    policy_dir = _materialize_capability_policy(ctx)

    options = ClaudeAgentOptions(
        cwd=str(ctx.worktree_path),
        allowed_tools=ctx.role_cfg.allowed_tools,
        system_prompt=ctx.role_cfg.phase_prompt,
        mcp_servers=mcp_servers,
        permission_mode="bypassPermissions",
    )
    _logger.info(
        "agent config: cwd=%s tools=%s mcps=%s",
        ctx.worktree_path,
        ctx.role_cfg.allowed_tools,
        ctx.role_cfg.allowed_mcps or ["jig"],
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

    def _user_message(content: str) -> dict:
        return {
            "type": "user",
            "session_id": "",
            "message": {"role": "user", "content": content},
            "parent_tool_use_id": None,
        }

    async def _prompt_stream():
        yield _user_message(initial_prompt)
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
            yield _user_message(_format_bus_event(msg))
            payload = msg.payload or {}
            if (
                payload.get("kind") == "ticket_updated"
                and payload.get("ticket_id") == ctx.ticket.id
                and payload.get("status") in terminal_values
            ):
                done.set()

    async def _emit(event_type: str, data: dict) -> None:
        """Log and optionally emit an event to the TUI."""
        if emitter is not None:
            try:
                await emitter.emit(JigEvent(type=event_type, data=data))
            except Exception:
                _logger.warning("emitter.emit raised; continuing", exc_info=True)

    final_text = ""
    # Short ticket prefix for log lines: first 8 chars of UUID
    tid = ctx.ticket.id[:8]
    tag = f"{ctx.role}:{tid}"
    # Build sandboxed transport when running inside the jig container
    transport = None
    if sandbox_available():
        # Only mount the policy + hook-bin dirs when we actually wrote
        # a rules.json for this spawn. Without declared capabilities
        # the settings.json registers no hooks, so the sandbox doesn't
        # need either mount — keeping bwrap's mount list minimal
        # reduces attack surface.
        hook_bin = _hook_bin_dir() if policy_dir is not None else None
        bwrap_cfg = BwrapConfig(
            worktree_host_path=ctx.worktree_path,
            policy_dir_host_path=policy_dir,
            hook_bin_host_path=hook_bin,
        )
        transport = BwrapTransport(prompt="", options=options, bwrap_config=bwrap_cfg)
        _logger.info("sandbox enabled for %s on %s", ctx.role, ctx.ticket.id)

    _logger.info(
        "launching claude agent for %s on %s in %s",
        ctx.role,
        ctx.ticket.id,
        ctx.worktree_path,
    )
    try:
        async for message in query(
            prompt=_prompt_stream(), options=options, transport=transport
        ):
            if isinstance(message, AssistantMessage):
                for block in message.content or []:
                    if isinstance(block, ToolUseBlock):
                        detail = _tool_detail(block.name, block.input or {})
                        _logger.info("[%s] tool: %s %s", tag, block.name, detail)
                        await _emit(
                            "agent_tool",
                            {
                                "role": ctx.role,
                                "ticket_id": ctx.ticket.id,
                                "tool": block.name,
                                "detail": detail,
                            },
                        )
                    elif isinstance(block, TextBlock):
                        short = _sanitize_for_tui(block.text)
                        if short:
                            _logger.info(
                                "[%s] text: %s",
                                tag,
                                _sanitize_for_tui(block.text, limit=2000),
                            )
                            await _emit(
                                "agent_text",
                                {
                                    "role": ctx.role,
                                    "ticket_id": ctx.ticket.id,
                                    "text": short,
                                },
                            )
            elif isinstance(message, SystemMessage):
                _logger.debug("[%s] system: %s", tag, message.subtype)
            elif isinstance(message, ResultMessage):
                final_text = message.result or ""
                _logger.info(
                    "[%s] completed: %s turns, %.1fs",
                    tag,
                    message.num_turns,
                    (message.duration_ms or 0) / 1000,
                )
            else:
                # Fallback for mocked result-like messages
                result = getattr(message, "result", None)
                if isinstance(result, str):
                    final_text = result
    except Exception:
        _logger.exception("claude agent SDK query failed for %s", ctx.role)
        raise
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
