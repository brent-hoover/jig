"""Bubblewrap sandbox for per-agent filesystem isolation.

When jig runs inside its Docker container, each Claude Code agent process
is wrapped in bubblewrap (bwrap).  The agent sees:

- ``/`` read-only  (full container filesystem for system libs / binaries)
- ``/workspace`` read-write  (the agent's git worktree)
- ``/tmp`` isolated tmpfs
- PID namespace unshared (can't see other processes)

The orchestrator itself runs *outside* bwrap — only agents are sandboxed.
"""

import logging
import os
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import ClaudeAgentOptions

_logger = logging.getLogger(__name__)


def sandbox_available() -> bool:
    """Return True when running inside the jig Docker container."""
    return bool(os.environ.get("JIG_IN_CONTAINER"))


@dataclass
class BwrapConfig:
    """Bubblewrap mount configuration for an agent sandbox."""

    worktree_host_path: Path
    """Host path to the agent's git worktree (mounted rw at /workspace)."""

    extra_ro_binds: list[tuple[str, str]] = field(default_factory=list)
    """Additional read-only bind mounts ``(host_path, sandbox_path)``."""

    extra_rw_binds: list[tuple[str, str]] = field(default_factory=list)
    """Additional read-write bind mounts ``(host_path, sandbox_path)``."""

    hide_paths: list[str] = field(default_factory=list)
    """Paths to overlay with empty tmpfs (hides content from agent)."""

    workspace: str = "/workspace"
    """Mount point inside the sandbox where the worktree appears."""

    def to_args(self) -> list[str]:
        """Build the bwrap argument list."""
        args: list[str] = [
            # Full container filesystem, read-only
            "--ro-bind", "/", "/",
            # Agent's worktree, read-write
            "--bind", str(self.worktree_host_path), self.workspace,
            # /proc and /dev are separate mount points — --ro-bind / /
            # doesn't capture them.  Bind-mount from the parent instead of
            # mounting fresh (--proc /proc requires privileges Docker blocks).
            "--ro-bind", "/proc", "/proc",
            "--dev-bind", "/dev", "/dev",
            # Isolated temp
            "--tmpfs", "/tmp",
        ]

        # Docker volume mounts are separate mount points that
        # --ro-bind / / doesn't capture.  Bind them explicitly.
        # Claude Code needs write access for OAuth token refresh.
        home = Path.home()
        claude_dir = home / ".claude"
        if claude_dir.is_dir():
            args.extend(["--bind", str(claude_dir), str(claude_dir)])
        claude_json = home / ".claude.json"
        if claude_json.is_file():
            args.extend(["--bind", str(claude_json), str(claude_json)])

        # Hide paths by overlaying with empty tmpfs
        for path in self.hide_paths:
            args.extend(["--tmpfs", path])

        # Extra mounts
        for src, dst in self.extra_ro_binds:
            args.extend(["--ro-bind", src, dst])
        for src, dst in self.extra_rw_binds:
            args.extend(["--bind", src, dst])

        # Working directory, env, and namespace isolation
        args.extend([
            "--chdir", self.workspace,
            "--setenv", "PWD", self.workspace,
            "--unshare-pid",
            "--die-with-parent",
        ])
        return args


class BwrapTransport(SubprocessCLITransport):
    """Wraps the Claude Code subprocess in a bubblewrap sandbox.

    Inherits all behaviour from ``SubprocessCLITransport``, overriding only
    ``_build_command()`` to prepend bwrap arguments to the Claude CLI
    command.  The orchestrator's ``connect()`` / ``read_messages()`` /
    ``write()`` / ``close()`` methods work unchanged — they just talk to
    bwrap's stdin/stdout instead of claude's directly.
    """

    def __init__(
        self,
        prompt: str | AsyncIterable[dict[str, Any]],
        options: ClaudeAgentOptions,
        bwrap_config: BwrapConfig,
    ) -> None:
        super().__init__(prompt, options)
        self._bwrap_config = bwrap_config
        # Nullify host cwd — bwrap sets the working directory via --chdir
        # and we set PWD via --setenv.  Keeping the host path here would
        # cause open_process(cwd=<host-worktree>) which is redundant and
        # would also set PWD to the host path in the env dict.
        self._cwd = None

    def _build_command(self) -> list[str]:
        """Prepend bwrap wrapper to the Claude CLI command."""
        claude_cmd = super()._build_command()
        cmd = ["bwrap", *self._bwrap_config.to_args(), "--", *claude_cmd]
        _logger.info(
            "sandboxed agent: worktree=%s workspace=%s",
            self._bwrap_config.worktree_host_path,
            self._bwrap_config.workspace,
        )
        _logger.debug("bwrap command (first 30 args): %s", cmd[:30])
        return cmd
