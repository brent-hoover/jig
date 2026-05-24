"""Manages the jig-controlled Claude config directory for agent spawns.

Agent subprocesses (spawned via the Claude Agent SDK) inherit the
operator's ``CLAUDE_CONFIG_DIR`` — defaulting to ``~/.claude/`` — which
carries personal hooks (e.g. a superpowers SessionStart hook) and the
global CLAUDE.md. Those cause personal skill-discovery workflows to leak
into reviewer and other non-interactive agents.

This module creates a minimal jig-controlled config directory that:

- Has no personal hooks or global CLAUDE.md.
- Installs jig's own skills as a proper Claude Code plugin so agents can
  invoke them via the Skill tool (MCP-tool reference, git conventions, etc.)

The config is written to ``~/.jig/claude-agent-config/`` on the host.

For bwrap spawns the orchestrator bind-mounts that directory into the
sandbox at ``SANDBOX_CLAUDE_CONFIG_PATH`` and sets ``CLAUDE_CONFIG_DIR``
accordingly; for non-bwrap spawns it sets ``CLAUDE_CONFIG_DIR`` to the
host path directly.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from jig.skill_loader import _parse_frontmatter

_logger = logging.getLogger(__name__)

# Sandbox-absolute path where the jig config dir is bind-mounted in bwrap.
SANDBOX_CLAUDE_CONFIG_PATH = "/jig/claude-config"

_PLUGIN_KEY = "jig-skills@jig"
_PLUGIN_VERSION = "1.0.0"


def _skill_files() -> list[tuple[str, str, str]]:
    """Return ``(name, description, body)`` for each jig skill."""
    pkg = resources.files("jig.skills")
    result: list[tuple[str, str, str]] = []
    for entry in sorted(pkg.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".md"):
            continue
        raw = entry.read_text(encoding="utf-8")
        front, body = _parse_frontmatter(raw)
        name = front.get("name", entry.name.removesuffix(".md"))
        description = front.get("description", "")
        result.append((name, description, body))
    return result


def ensure_agent_config_dir(*, sandbox_config_path: str | None = None) -> Path:
    """Create or refresh the jig-managed Claude config directory.

    Writes the plugin structure to ``~/.jig/claude-agent-config/``.  The
    ``installPath`` values in ``installed_plugins.json`` are rooted at
    ``sandbox_config_path`` when provided (for bwrap, where the host dir is
    mounted at a different path), or at the host path otherwise.

    Returns the host path of the config directory so callers can mount or
    reference it.
    """
    host_dir = Path.home() / ".jig" / "claude-agent-config"
    host_dir.mkdir(parents=True, exist_ok=True)

    config_base = Path(sandbox_config_path) if sandbox_config_path else host_dir

    _write_settings(host_dir)
    _write_plugin(host_dir, config_base)

    _logger.debug("jig agent config dir ready at %s (config_base=%s)", host_dir, config_base)
    return host_dir


def _write_settings(config_dir: Path) -> None:
    (config_dir / "settings.json").write_text(
        json.dumps({}, indent=2), encoding="utf-8"
    )


def _write_plugin(config_dir: Path, config_base: Path) -> None:
    plugin_dir = (
        config_dir / "plugins" / "cache" / "jig" / "jig-skills" / _PLUGIN_VERSION
    )
    plugin_dir.mkdir(parents=True, exist_ok=True)

    skills_dir = plugin_dir / "skills"
    skills_dir.mkdir(exist_ok=True)

    for name, description, body in _skill_files():
        skill_dir = skills_dir / name
        skill_dir.mkdir(exist_ok=True)
        front = f"---\nname: {name}\n"
        if description:
            front += f"description: {json.dumps(description)}\n"
        front += "---\n\n"
        (skill_dir / "SKILL.md").write_text(front + body, encoding="utf-8")

    # installPath uses the sandbox-visible base so Claude Code can resolve
    # skill files inside the bwrap namespace.
    install_path = str(
        config_base / "plugins" / "cache" / "jig" / "jig-skills" / _PLUGIN_VERSION
    )
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    installed = {
        "version": 2,
        "plugins": {
            _PLUGIN_KEY: [
                {
                    "scope": "user",
                    "installPath": install_path,
                    "version": _PLUGIN_VERSION,
                    "installedAt": now,
                    "lastUpdated": now,
                    "gitCommitSha": None,
                }
            ]
        },
    }
    installed_path = config_dir / "plugins" / "installed_plugins.json"
    installed_path.parent.mkdir(parents=True, exist_ok=True)
    installed_path.write_text(json.dumps(installed, indent=2), encoding="utf-8")


__all__ = ["SANDBOX_CLAUDE_CONFIG_PATH", "ensure_agent_config_dir"]
