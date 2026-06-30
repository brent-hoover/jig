"""Per-agent MCP server assembly — owned by the runtime (Epic 3 MVP, task 3).

Assembling the MCP server *set* a real agent run gets is the runtime's job: build
the in-process ``jig`` server (the tool-registration factory stays in
``jig.mcp_server``), resolve any external stdio MCPs the role allows, and return
the ``{name: server}`` map the SDK is handed. Centralising it here makes the
"Runtime owns the MCP lifecycle" boundary real in the dependency graph — the
piece a headless, daemon-free pipeline needs to control or substitute.

Isolation invariant (the evaluability north star): this module reaches only
``ctx`` + stores + the jig tool factory + the filesystem (``~/.claude``). It must
NOT import the orchestrator / daemon / TUI / app layer — a fitness test pins this.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from jig.mcp_server import create_agent_mcp_server
from jig.persistence import list_roles
from jig.runtime.spawn_context import AgentSpawnContext

if TYPE_CHECKING:
    from jig.analytics.emitter import EventEmitter

_logger = logging.getLogger(__name__)


def effective_ticket_base_ref(ctx: AgentSpawnContext) -> str:
    """Base ref for the reviewer's ``reviewer_get_diff`` tool.

    review-severity-binary §4: on a re-review round ``ctx.delta_base`` is the
    last-reviewed commit, so the reviewer's diff contains only the fix delta
    (matching the prompt's Re-review Scope note). On first-round reviews it is
    None and we fall back to the project default branch (full-ticket diff).
    """
    return ctx.delta_base or ctx.project.default_branch


def resolve_external_mcps(allowed_mcps: list[str]) -> dict[str, Any]:
    """Resolve allowed MCP names to stdio server configs.

    Searches two locations for each name:
    1. User-level ``~/.claude/.mcp.json`` (``mcpServers`` key)
    2. Installed plugin cache (``~/.claude/plugins/cache/*/*/.mcp.json``)

    Returns a dict of ``{server_name: McpStdioServerConfig}`` suitable for
    merging into the ``mcp_servers`` option. A malformed config (bad JSON, or a
    non-dict root) is logged and skipped — config resolution is best-effort and
    must not crash an agent spawn.
    """
    if not allowed_mcps:
        return {}

    result: dict[str, Any] = {}
    remaining = set(allowed_mcps)

    # 1. User-level .mcp.json
    user_mcp = Path.home() / ".claude" / ".mcp.json"
    if user_mcp.exists():
        try:
            root = json.loads(user_mcp.read_text())
            servers = root.get("mcpServers", {}) if isinstance(root, dict) else {}
            if not isinstance(servers, dict):
                servers = {}
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
                        if not isinstance(servers, dict):
                            _logger.warning(
                                "Ignoring %s: root is not an object", mcp_json
                            )
                            break
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


def build_agent_mcp_servers(
    ctx: AgentSpawnContext, *, can_waive: frozenset[str]
) -> dict[str, Any]:
    """Assemble the ``{name: server}`` MCP map for one real agent run.

    Builds the in-process ``jig`` server via the tool-registration factory and
    merges any external stdio MCPs the role allows. ``can_waive`` is compiled by
    the spawn's capability-policy step (which also writes the enforcement
    artefacts) and flows in here; everything else is derived from ``ctx``.

    The ``jig`` key is always present; external servers are added under their own
    names. A duplicate external name would shadow ``jig`` only if a role declared
    an MCP literally named ``jig`` — not a real configuration.
    """
    valid_roles = frozenset(r.role for r in list_roles(ctx.project.path_or_default()))
    phase_questions_to: frozenset[str] = (
        frozenset(ctx.phase.questions_to) if ctx.phase else frozenset()
    )
    phase_escalation_targets: frozenset[str] = (
        frozenset(ctx.phase.escalation_targets) if ctx.phase else frozenset()
    )

    jig_server = create_agent_mcp_server(
        tickets=ctx.tickets,
        threads=ctx.threads,
        memory=ctx.memory,
        bus=ctx.bus,
        agent_role=ctx.role,
        agent_cfg=ctx.role_cfg,
        worktree_path=ctx.worktree_path,
        project_path=ctx.project.path_or_default(),
        valid_roles=valid_roles,
        package_manager=ctx.project.package_manager,
        checkpoints=ctx.checkpoints,
        phase_name=ctx.phase.name if ctx.phase else "",
        can_waive=can_waive,
        phase_questions_to=phase_questions_to,
        phase_escalation_targets=phase_escalation_targets,
        ticket_id=ctx.ticket.id,
        cycle=ctx.cycle,
        adjudication=ctx.adjudication_collector,
        # ``analytics_emitter`` is typed ``object | None`` on the context (kept
        # loose to avoid a heavy import there); at runtime it's the orchestrator's
        # EventEmitter or None — narrow honestly rather than suppress arg-type.
        analytics_emitter=cast("EventEmitter | None", ctx.analytics_emitter),
        ticket_base_ref=effective_ticket_base_ref(ctx),
    )

    mcp_servers: dict[str, Any] = {"jig": jig_server}
    external = resolve_external_mcps(ctx.role_cfg.allowed_mcps)
    mcp_servers.update(external)
    if external:
        _logger.info("external MCPs for %s: %s", ctx.role, list(external.keys()))
    return mcp_servers


__all__ = [
    "build_agent_mcp_servers",
    "effective_ticket_base_ref",
    "resolve_external_mcps",
]
