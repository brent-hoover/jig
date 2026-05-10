"""Agent runner — spawns Claude Code agents via the SDK in streaming input mode."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ThinkingConfigAdaptive,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from jig.capability_compiler import compile as compile_capabilities
from jig.capability_compiler import materialize as materialize_capabilities
from jig.context_resolver import resolve_context_uris
from jig.environment import load_environment_md
from jig.events import EventEmitter, JigEvent
from jig.logging_setup import (
    _agent_id_var,
    _phase_var,
    _role_var,
    _ticket_id_var,
)
from jig.mcp_server import create_agent_mcp_server
from jig.persistence import list_roles, load_conventions
from jig.prompt_builder import build_initial_prompt
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.sandbox import BwrapConfig, BwrapTransport, sandbox_available
from jig.skill_loader import load_all_skills, match_skills
from jig.store import Message
from jig.thread import SystemEvent
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


def _sanitize_prose_for_tui(text: str) -> str:
    """Strip ANSI/control chars but keep newlines and full content for prose."""
    if not text:
        return ""
    text = _ANSI_RE.sub("", text)
    text = _CTRL_RE.sub("", text)
    return text.strip()


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


def _thinking_config() -> ThinkingConfigAdaptive:
    """Return the ThinkingConfig to pass to the SDK.

    Adaptive lets the model choose its thinking budget per turn. If
    cost becomes a concern, swap to ThinkingConfigEnabled(budget_tokens=N)
    behind a single knob here.
    """
    return ThinkingConfigAdaptive(type="adaptive")


# Cap for raw logged block text. Thinking blocks + tool results can
# be large; truncate to keep the log file manageable. A companion
# ``*_truncated`` DEBUG line records the real length so post-mortem
# readers know to fetch the full content some other way if needed.
_LOG_TRUNCATE_BYTES = 32 * 1024


# Built-in Claude Code tools that strict-tools roles (PO, SA,
# spec-generator) should never reach for. These are exploratory or
# mutating tools that are out of scope for narrow-conversation roles
# whose entire job is to talk through MCP. ``ToolSearch`` stays allowed
# because it's required to load deferred MCP tool schemas. ``Read`` is
# denied by default — the PO's brief lives in the MCP store, not on
# disk, and letting Read through caused agents to chase non-existent
# `brief.md` files. Roles that genuinely need Read (reviewers, SA,
# planner_pm) opt back in by listing ``Read`` in ``allowed_tools``.
_STRICT_DENY_BUILTINS: frozenset[str] = frozenset(
    {
        "Bash",
        "Edit",
        "Write",
        "NotebookEdit",
        "Glob",
        "Grep",
        "Read",
        "Agent",
        "WebSearch",
        "WebFetch",
    }
)


def _strict_disallowed_tools(allowed_tools: list[str]) -> list[str]:
    """Compute the SDK ``disallowed_tools`` list for a strict-tools role.

    Subtracts whatever the role explicitly lists in ``allowed_tools``
    from the dangerous-builtin set, so a strict role can opt back into
    e.g. ``Bash`` by naming it. Returned sorted for determinism in
    logs and tests.
    """
    return sorted(_STRICT_DENY_BUILTINS - set(allowed_tools))


@dataclass
class RunAgentResult:
    status: str  # "success" | "failed" | "blocked" | "needs_info"
    final_text: str
    total_cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    # SF-I5: surface non-fatal post-run issues (e.g. failure to write
    # the ``agent_run`` SystemEvent) so callers and analytics can tell
    # "succeeded fully" apart from "succeeded but the audit trail is
    # incomplete". None when there's nothing to report.
    warnings: list[str] = field(default_factory=list)


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

    # Phase 4.9 — graph-narrowed context (JIG_GRAPH_CONTEXT=1).
    # Appends contracts for modules in the ticket's depth-1 neighborhood
    # so the agent has the relevant integration surfaces without loading
    # every artifact. Behind a flag for before/after eval comparison.
    import os as _os

    if _os.environ.get("JIG_GRAPH_CONTEXT"):
        from jig.graph_context import build_graph_context

        try:
            graph_ctx = await build_graph_context(
                ctx.ticket, project_path, depth=1
            )
            resolved_context = resolved_context + graph_ctx
        except Exception:
            pass  # Narrowing is best-effort; fall through on any error

    all_roles = list_roles(ctx.project.path_or_default())
    conventions_md = load_conventions(project_path)

    # For evaluator spawns the orchestrator stamps a structured bundle
    # (handoff id + check results) onto ``initial_bus_message``; the
    # prompt builder consumes it directly. Non-evaluator spawns pass
    # ``None`` and the builder ignores it.
    evaluator_bundle = (
        ctx.initial_bus_message if ctx.spawn_reason == SpawnReason.EVALUATOR else None
    )
    conflict_bundle = (
        ctx.initial_bus_message
        if ctx.spawn_reason == SpawnReason.CONFLICT_RESOLVER
        else None
    )
    replan_bundle = (
        ctx.initial_bus_message if ctx.spawn_reason == SpawnReason.REPLAN else None
    )

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
        evaluator_bundle=evaluator_bundle,
        conflict_bundle=conflict_bundle,
        replan_bundle=replan_bundle,
        conventions_md=conventions_md,
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


@dataclass(frozen=True)
class _CapabilityMaterialization:
    """Return value of :func:`_materialize_capability_policy`. Splits
    out the sandbox policy dir (bind-mounted for hook scripts) from
    the orchestrator-side waiver set (consulted by thread_mcp handlers
    regardless of sandbox availability)."""

    policy_dir: Path | None
    can_waive: frozenset[str]


def _materialize_capability_policy(
    ctx: AgentSpawnContext,
) -> _CapabilityMaterialization:
    """Compile capability policy for a spawn and (when the sandbox is
    available) write the hook enforcement artefacts.

    Returns both the policy directory (for bind-mount — may be ``None``
    when no declaration exists or sandbox is unavailable) and the
    compiled ``can_waive`` frozenset. Waiver authorization runs
    orchestrator-side (in :mod:`jig.thread_mcp`), so the frozenset is
    surfaced independently of whether sandbox hooks were materialised.

    Location of the hook artefacts (when emitted):

    * ``rules.json`` → ``<project>/.jig/runtime/<ticket_id>/policy/``
    * ``.claude/settings.json`` → ``<worktree>/.claude/settings.json``

    Materialisation failures log + return ``policy_dir=None`` but keep
    the compiled ``can_waive`` — a filesystem glitch shouldn't
    deauthorize the agent's waiver tokens, which aren't enforced via
    the hooks anyway.
    """
    role_caps = ctx.role_cfg.capabilities
    phase_caps = ctx.phase.capability_overrides if ctx.phase else None

    if role_caps is None and phase_caps is None:
        return _CapabilityMaterialization(policy_dir=None, can_waive=frozenset())

    rules = compile_capabilities(role_caps, phase_caps)
    can_waive = frozenset(rules.waivers.can_waive)

    if not sandbox_available():
        _logger.debug(
            "skipping capability materialisation for %s on %s: "
            "no sandbox available (hook paths are container-absolute)",
            ctx.role,
            ctx.ticket.id,
        )
        return _CapabilityMaterialization(policy_dir=None, can_waive=can_waive)

    try:
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
        return _CapabilityMaterialization(policy_dir=policy_dir, can_waive=can_waive)
    except OSError:
        _logger.exception(
            "capability materialisation failed for %s on %s",
            ctx.role,
            ctx.ticket.id,
        )
        return _CapabilityMaterialization(policy_dir=None, can_waive=can_waive)


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
    # Stamp correlation fields for this spawn. ticket_id / phase /
    # role are already set by the orchestrator in its per-ticket
    # path, but run_agent is also called by evaluator spawns and
    # tests so re-set defensively. The finally block at the end of
    # this function resets these tokens.
    _agent_id = f"{ctx.role}:{ctx.ticket.id[:8]}"
    _tid_token = _ticket_id_var.set(ctx.ticket.id)
    _phase_token = _phase_var.set(ctx.phase.name if ctx.phase else None)
    _role_token = _role_var.set(ctx.role)
    _agent_token = _agent_id_var.set(_agent_id)
    try:
        initial_prompt = await build_agent_prompt(ctx)
        _logger.info("prompt built for %s (%d chars)", ctx.role, len(initial_prompt))
        _logger.debug("--- SYSTEM PROMPT [%s] ---\n%s", ctx.role, ctx.role_cfg.phase_prompt)
        _logger.debug("--- INITIAL PROMPT [%s] ---\n%s", ctx.role, initial_prompt)

        # Phase 5 Task F: compile capability policy (role base + phase
        # override) and materialize the two enforcement artefacts before
        # Claude Code starts. Only writes files when a declaration actually
        # exists — a spawn with no declared policy gets no .claude/settings
        # overwrite and no rules.json clutter. Task G's hook scripts read
        # rules.json at tool-eval time. Hoisted before the MCP server so
        # the compiled ``can_waive`` set can flow into the factory.
        cap = _materialize_capability_policy(ctx)

        all_roles = list_roles(ctx.project.path_or_default())
        # Phase 5 Task K: per-phase thread-target allow-lists feed into
        # thread_ask / thread_escalate as hard refusals. Missing ctx.phase
        # (standalone spawns, tests) or empty lists keep today's permissive
        # behavior — the MCP factory treats an empty frozenset as "no
        # restriction declared".
        phase_q_to: frozenset[str] = (
            frozenset(ctx.phase.questions_to) if ctx.phase else frozenset()
        )
        phase_esc_targets: frozenset[str] = (
            frozenset(ctx.phase.escalation_targets) if ctx.phase else frozenset()
        )

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
            can_waive=cap.can_waive,
            phase_questions_to=phase_q_to,
            phase_escalation_targets=phase_esc_targets,
            ticket_id=ctx.ticket.id,
            # Block 2 — analytics emitter rides through so MCP tool
            # handlers that emit analytics events (ontology edits, etc.)
            # actually emit when invoked from a real agent.
            analytics_emitter=ctx.analytics_emitter,
        )

        mcp_servers: dict = {"jig": mcp_server}
        external = _resolve_external_mcps(ctx.role_cfg.allowed_mcps)
        mcp_servers.update(external)
        if external:
            _logger.info("external MCPs for %s: %s", ctx.role, list(external.keys()))

        # Strict-tools roles (PO, SA, spec-generator) get a curated
        # disallow list of dangerous built-ins so e.g. PO can't reach
        # for Bash/Glob/Agent and start exploring the filesystem.
        # ``bypassPermissions`` is required because there's no
        # interactive operator to approve prompts; ``disallowed_tools``
        # is enforced even under bypass. Anything the role explicitly
        # lists in ``allowed_tools`` is removed from the deny list so a
        # role can opt into a built-in by naming it.
        disallowed: list[str] = []
        if ctx.role_cfg.strict_tools:
            disallowed = _strict_disallowed_tools(ctx.role_cfg.allowed_tools)
        # Track E MVP — propagate the orchestrator-built env map (one
        # entry per provisioned dev service: JIG_DEV_<SERVICE_ID>_URL).
        # Empty / None means no extra env (typical bones runs and any
        # role that doesn't touch dev services).
        sdk_kwargs: dict = {}
        if ctx.extra_env:
            sdk_kwargs["env"] = dict(ctx.extra_env)

        options = ClaudeAgentOptions(
            cwd=str(ctx.worktree_path),
            allowed_tools=ctx.role_cfg.allowed_tools,
            disallowed_tools=disallowed,
            system_prompt=ctx.role_cfg.phase_prompt,
            mcp_servers=mcp_servers,
            permission_mode="bypassPermissions",
            thinking=_thinking_config(),
            **sdk_kwargs,
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
        cost_usd: float | None = None
        tokens_in: int | None = None
        tokens_out: int | None = None
        run_warnings: list[str] = []
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
            hook_bin = _hook_bin_dir() if cap.policy_dir is not None else None
            # ctx.extra_env (e.g. JIG_DEV_<service>_URL) won't propagate
            # through SDK options.env once the sandbox uses --clearenv,
            # so explicit --setenv pairs are needed here.
            extra_setenv: tuple[tuple[str, str], ...] = (
                tuple((k, str(v)) for k, v in ctx.extra_env.items())
                if ctx.extra_env
                else ()
            )
            # Hide the orchestrator's project mount and any sibling
            # state from the agent. The agent's worktree is bind-mounted
            # at /workspace independently, so /project can be tmpfs-ed
            # without losing access to its own files. This narrows the
            # rootfs view down toward "only its worktree" — see SEC-4
            # in v2-review-findings-security.md.
            hide_paths = ["/project", "/home/jig/.ssh", "/home/jig/.gitconfig"]
            bwrap_cfg = BwrapConfig(
                worktree_host_path=ctx.worktree_path,
                policy_dir_host_path=cap.policy_dir,
                hook_bin_host_path=hook_bin,
                extra_setenv=extra_setenv,
                hide_paths=hide_paths,
            )
            transport = BwrapTransport(prompt="", options=options, bwrap_config=bwrap_cfg)
            _logger.info("sandbox enabled for %s on %s", ctx.role, ctx.ticket.id)

        _logger.info(
            "launching claude agent for %s on %s in %s",
            ctx.role,
            ctx.ticket.id,
            ctx.worktree_path,
        )

        # Banner: announce this agent run to the TUI with enough context
        # for it to draw a labeled section divider (role, ticket, phase).
        # Without this the operator can't tell which output belongs to
        # which agent in a multi-agent transcript.
        await _emit(
            "agent_start",
            {
                "role": ctx.role,
                "ticket_id": ctx.ticket.id,
                "ticket_title": ctx.ticket.title or "",
                "phase": ctx.phase.name if ctx.phase else None,
            },
        )

        # Heartbeat: emit agent_thinking events every second so the TUI's
        # Activity zone (driven by agent_thinking) reflects that the agent
        # is alive even before the SDK streams any tool/text blocks back.
        # Without this the sidebar shows "no agents running" until the
        # first AssistantMessage arrives — which can be many seconds.
        heartbeat_loop = asyncio.get_running_loop()
        heartbeat_start = heartbeat_loop.time()
        heartbeat_done = asyncio.Event()

        async def _heartbeat() -> None:
            await _emit(
                "agent_thinking",
                {"role": ctx.role, "ticket_id": ctx.ticket.id, "elapsed": 0, "active": True},
            )
            try:
                while not heartbeat_done.is_set():
                    try:
                        await asyncio.wait_for(heartbeat_done.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        elapsed = int(heartbeat_loop.time() - heartbeat_start)
                        await _emit(
                            "agent_thinking",
                            {"role": ctx.role, "ticket_id": ctx.ticket.id, "elapsed": elapsed, "active": True},
                        )
                        if ctx.on_thinking is not None:
                            ctx.on_thinking()
            finally:
                try:
                    await _emit(
                        "agent_thinking",
                        {"role": ctx.role, "ticket_id": ctx.ticket.id, "elapsed": 0, "active": False},
                    )
                except Exception:
                    pass

        heartbeat_task = asyncio.create_task(_heartbeat())

        try:
            # Map ToolUseBlock.id → tool name so we can label
            # ToolResultBlocks (which only carry the use id, not the name)
            # when emitting result events for the CLI/TUI.
            tool_names_by_use_id: dict[str, str] = {}

            async for message in query(
                prompt=_prompt_stream(), options=options, transport=transport
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content or []:
                        if isinstance(block, ToolUseBlock):
                            detail = _tool_detail(block.name, block.input or {})
                            tool_names_by_use_id[block.id] = block.name
                            _logger.info("[%s] tool: %s %s", tag, block.name, detail)
                            _logger.debug(
                                "[%s] tool_input: id=%s %s",
                                tag,
                                block.id,
                                json.dumps(block.input or {}, default=str),
                            )
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
                            full_text = _sanitize_prose_for_tui(block.text)
                            if full_text:
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
                                        "text": full_text,
                                    },
                                )
                        elif isinstance(block, ThinkingBlock):
                            # Post-mortem only — not emitted to TUI.
                            raw = block.thinking or ""
                            raw_bytes = raw.encode("utf-8")
                            if len(raw_bytes) > _LOG_TRUNCATE_BYTES:
                                truncated = raw_bytes[:_LOG_TRUNCATE_BYTES].decode(
                                    "utf-8", errors="ignore"
                                )
                                _logger.debug(
                                    "[%s] thinking_truncated: full_bytes=%d",
                                    tag,
                                    len(raw_bytes),
                                )
                            else:
                                truncated = raw
                            _logger.debug("[%s] thinking: %s", tag, truncated)
                elif isinstance(message, UserMessage):
                    content = message.content
                    if isinstance(content, str):
                        # Rare but allowed by the SDK type union; there are no
                        # tool-result blocks to capture in a bare-string
                        # user message.
                        pass
                    else:
                        for block in content or []:
                            if isinstance(block, ToolResultBlock):
                                raw = (
                                    block.content
                                    if block.content is not None
                                    else ""
                                )
                                if not isinstance(raw, str):
                                    # SDK may give back a list of dicts;
                                    # serialise.
                                    raw = json.dumps(raw, default=str)
                                raw_bytes = raw.encode("utf-8")
                                if len(raw_bytes) > _LOG_TRUNCATE_BYTES:
                                    truncated = raw_bytes[
                                        :_LOG_TRUNCATE_BYTES
                                    ].decode("utf-8", errors="ignore")
                                    _logger.debug(
                                        "[%s] tool_result_truncated: "
                                        "id=%s full_bytes=%d",
                                        tag,
                                        block.tool_use_id,
                                        len(raw_bytes),
                                    )
                                else:
                                    truncated = raw
                                is_error = bool(block.is_error)
                                _logger.debug(
                                    "[%s] tool_result: id=%s is_error=%s %s",
                                    tag,
                                    block.tool_use_id,
                                    is_error,
                                    truncated,
                                )
                                tool_name = tool_names_by_use_id.get(
                                    block.tool_use_id, "tool"
                                )
                                excerpt = _sanitize_for_tui(raw, limit=200)
                                await _emit(
                                    "agent_tool_result",
                                    {
                                        "role": ctx.role,
                                        "ticket_id": ctx.ticket.id,
                                        "tool": tool_name,
                                        "is_error": is_error,
                                        "excerpt": excerpt,
                                    },
                                )
                elif isinstance(message, SystemMessage):
                    _logger.debug("[%s] system: %s", tag, message.subtype)
                elif isinstance(message, ResultMessage):
                    final_text = message.result or ""
                    cost_usd = message.total_cost_usd
                    usage = message.usage or {}
                    tokens_in = usage.get("input_tokens")
                    tokens_out = usage.get("output_tokens")
                    _logger.info(
                        "[%s] completed: %s turns, %.1fs, $%.4f",
                        tag,
                        message.num_turns,
                        (message.duration_ms or 0) / 1000,
                        cost_usd or 0.0,
                    )
                    # Tell ``_prompt_stream`` to exhaust so the SDK can
                    # close stdin and the bundled ``claude`` subprocess
                    # can exit. Without this we deadlock between
                    # phases: the agent emits ResultMessage, but the
                    # generator keeps polling for a terminal ticket
                    # status that never arrives (orchestrator only sets
                    # it AFTER run_agent returns), so the subprocess
                    # waits forever for the next stdin line.
                    done.set()
                    # Post an agent_run SystemEvent so the story view
                    # gets per-spawn timing without having to parse logs.
                    # SF-I5: a failure to post here means we lose the
                    # canonical run metadata for the story view; record
                    # a warning on the result so the caller can see the
                    # gap rather than silently treating it as a clean run.
                    try:
                        preview = _sanitize_for_tui(final_text, limit=500)
                        await ctx.threads.post(
                            SystemEvent(
                                ticket_id=ctx.ticket.id,
                                author="orchestrator",
                                event_type="agent_run",
                                content=f"{ctx.role} ran {message.num_turns} turns",
                                payload={
                                    "role": ctx.role,
                                    "num_turns": message.num_turns,
                                    "duration_ms": message.duration_ms or 0,
                                    "spawn_reason": ctx.spawn_reason.value,
                                    "result_preview": preview,
                                },
                            )
                        )
                    except Exception as exc:
                        run_warnings.append(
                            f"agent_run SystemEvent post failed: {exc!r}"
                        )
                        _logger.warning(
                            "failed to post agent_run SystemEvent", exc_info=True,
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
            # Stop the heartbeat task and wait for it to emit the final
            # active:False event so the TUI Activity zone clears the role.
            heartbeat_done.set()
            try:
                await heartbeat_task
            except Exception:
                _logger.warning("heartbeat task raised", exc_info=True)
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
        return RunAgentResult(
            status=status,
            final_text=final_text,
            total_cost_usd=cost_usd,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            warnings=list(run_warnings),
        )
    finally:
        _ticket_id_var.reset(_tid_token)
        _phase_var.reset(_phase_token)
        _role_var.reset(_role_token)
        _agent_id_var.reset(_agent_token)


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
    # Ticket is IN_PROGRESS when run_agent returns (orchestrator resolves it
    # after all phases complete). A non-failure status means the agent ran
    # without explicitly blocking or failing, so report success.
    if status == TicketStatus.BLOCKED:
        return "blocked"
    if status == TicketStatus.NEEDS_INFO:
        return "needs_info"
    if status == TicketStatus.FAILED:
        return "failed"
    return "success"
