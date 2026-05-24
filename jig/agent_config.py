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
import shutil
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


def ensure_agent_config_dir(
    *,
    skill_names: list[str] | None = None,
    sandbox_config_path: str | None = None,
    spawn_dir_name: str | None = None,
) -> Path:
    """Create or refresh the jig-managed Claude config directory.

    When ``spawn_dir_name`` is given, writes to
    ``~/.jig/claude-agent-configs/<spawn_dir_name>/`` — an isolated directory
    for a single agent spawn. Callers should remove this directory after the
    agent exits (``shutil.rmtree(path, ignore_errors=True)``).

    When ``spawn_dir_name`` is omitted, falls back to the shared
    ``~/.jig/claude-agent-config/`` path (legacy / non-concurrent use).

    The ``installPath`` values in ``installed_plugins.json`` are rooted at
    ``sandbox_config_path`` when provided (for bwrap, where the host dir is
    mounted at a different path), or at the host path otherwise.

    ``skill_names``: when provided, only skills whose ``name`` is in this list
    are included in the plugin. When ``None`` or empty, all jig skills are
    included (broad default for roles that don't declare specific skills).

    Returns the host path of the config directory so callers can mount or
    reference it.
    """
    if spawn_dir_name:
        host_dir = Path.home() / ".jig" / "claude-agent-configs" / spawn_dir_name
    else:
        host_dir = Path.home() / ".jig" / "claude-agent-config"
    host_dir.mkdir(parents=True, exist_ok=True)

    config_base = Path(sandbox_config_path) if sandbox_config_path else host_dir

    _write_settings(host_dir)
    _write_plugin(host_dir, config_base, skill_names=skill_names or [])

    _logger.debug(
        "jig agent config dir ready at %s (skills=%s config_base=%s)",
        host_dir,
        skill_names,
        config_base,
    )
    return host_dir


def _write_settings(config_dir: Path) -> None:
    (config_dir / "settings.json").write_text(
        json.dumps({}, indent=2), encoding="utf-8"
    )


def _write_plugin(
    config_dir: Path,
    config_base: Path,
    *,
    skill_names: list[str],
) -> None:
    plugin_dir = (
        config_dir / "plugins" / "cache" / "jig" / "jig-skills" / _PLUGIN_VERSION
    )
    plugin_dir.mkdir(parents=True, exist_ok=True)

    skills_dir = plugin_dir / "skills"
    # Wipe and recreate so stale skill dirs from a prior run (different
    # skill_names list) don't linger and show up in the session reminder.
    if skills_dir.exists():
        shutil.rmtree(skills_dir)
    skills_dir.mkdir()

    all_skills = _skill_files()
    # Filter to the declared list when non-empty; include all otherwise.
    if skill_names:
        found = {n for n, _, _ in all_skills}
        for name in skill_names:
            if name not in found:
                _logger.warning(
                    "skill %r declared in role but not found in jig/skills/", name
                )
        selected = [(n, d, b) for n, d, b in all_skills if n in skill_names]
    else:
        selected = all_skills

    for name, description, body in selected:
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
