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


def _normalise_mount_path(path: str) -> tuple[str, ...]:
    """Split a sandbox-absolute mount path into its non-empty segments.

    Used to compare mount destinations for overlap (:meth:`BwrapConfig.
    _reject_reserved`). Trailing/leading slashes and empty segments are
    stripped so ``/jig/bin`` and ``/jig/bin/`` compare equal, while
    ``/jig/bin`` and ``/jig/binned`` stay distinct."""

    return tuple(part for part in path.split("/") if part)


# Env vars forwarded into the sandbox by default. Anything else from
# the orchestrator's environment is dropped by ``--clearenv`` so an
# agent can't read JIG_*, GIT_*, or other host secrets just by running
# ``env`` or reading ``/proc/self/environ``. The bundled claude CLI
# (SDK >= 0.1.80) authenticates via ``CLAUDE_CODE_OAUTH_TOKEN`` since
# the on-host credentials live in the system keychain and the
# Docker-mounted ``~/.claude.json`` only carries profile info, not
# access tokens. The token is no more sensitive than the agent's
# ability to make Claude API calls — it's already implicit in the
# agent's role — so passing it through is the minimal trust we can give
# without breaking auth entirely.
_DEFAULT_PASSTHROUGH_ENV: tuple[str, ...] = (
    "HOME",
    "PATH",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TMPDIR",
    "SHELL",
    # The bundled claude CLI (SDK >= 0.1.80) writes logs/state to its
    # config dir. Inside bwrap, ~/.claude is read-only (ro-bind from the
    # container) so we need CLAUDE_CONFIG_DIR pointing to a writable path.
    # /tmp is tmpfs inside bwrap, so any sub-path there is writable.
    "CLAUDE_CONFIG_DIR",
    # OAuth token for the bundled claude CLI. The mounted ~/.claude.json
    # only has profile info (host credentials are in the keychain), so
    # auth inside bwrap requires this env var.
    "CLAUDE_CODE_OAUTH_TOKEN",
)


@dataclass
class BwrapConfig:
    """Bubblewrap mount configuration for an agent sandbox."""

    worktree_host_path: Path
    """Host path to the agent's git worktree (mounted rw at /workspace)."""

    policy_dir_host_path: Path | None = None
    """Host directory containing ``rules.json`` for this spawn.

    When set, the directory is bind-mounted read-only at ``/jig/policy/``
    inside the sandbox. The capability enforcement hook scripts
    (``check-bash``, ``check-write``, ``check-path``) read
    ``/jig/policy/rules.json`` on every tool call. Leave ``None`` for
    spawns with no declared capabilities — in that case
    ``.claude/settings.json`` registers no hooks, so the policy path
    is never dereferenced."""

    hook_bin_host_path: Path | None = None
    """Host directory containing the capability enforcement hook
    scripts. Bind-mounted read-only at ``/jig/bin/`` when set. Paired
    with ``policy_dir_host_path``: the hooks registered in
    ``.claude/settings.json`` point at ``/jig/bin/check-*``, so the
    two mounts must be applied together for policy to fire."""

    extra_ro_binds: list[tuple[str, str]] = field(default_factory=list)
    """Additional read-only bind mounts ``(host_path, sandbox_path)``."""

    extra_rw_binds: list[tuple[str, str]] = field(default_factory=list)
    """Additional read-write bind mounts ``(host_path, sandbox_path)``."""

    hide_paths: list[str] = field(default_factory=list)
    """Paths to overlay with empty tmpfs (hides content from agent)."""

    workspace: str = "/workspace"
    """Mount point inside the sandbox where the worktree appears."""

    passthrough_env_keys: tuple[str, ...] = _DEFAULT_PASSTHROUGH_ENV
    """Names of env vars forwarded from the orchestrator process into
    the sandbox via ``--setenv``. Anything not in this list is dropped
    by ``--clearenv``. ``CLAUDE_CODE_OAUTH_TOKEN`` IS forwarded — the
    bundled claude CLI needs it to authenticate inside bwrap, where
    ``~/.claude.json`` is read-only and host-keychain credentials are
    unreachable. ``ANTHROPIC_API_KEY`` is NOT forwarded — agents use
    OAuth, not the API key."""

    extra_setenv: tuple[tuple[str, str], ...] = ()
    """Additional ``(key, value)`` env pairs to set inside the sandbox.
    Used for orchestrator-curated values like ``JIG_DEV_<service>_URL``
    which aren't in the orchestrator's own environ."""

    # Sandbox-absolute mount points for the capability-policy artefacts.
    # These match ``jig.capability_compiler.SANDBOX_RULES_PATH`` and
    # ``SANDBOX_HOOK_BIN`` — if either constant moves, update both.
    policy_mount: str = "/jig/policy"
    hook_bin_mount: str = "/jig/bin"

    def __post_init__(self) -> None:
        """Reject extra mounts that would collide with the reserved
        capability-policy mount points.

        Ordering alone is not enough: bwrap honours later ``--bind``
        entries, so an ``extra_rw_binds`` pair targeting ``/jig/bin``
        would overwrite the earlier read-only mount, and a
        ``hide_paths`` entry targeting ``/jig/policy`` would tmpfs-
        overlay the rules. Reject both at config time rather than
        hoping the argument order prevents it. We also reject nested
        paths (``/jig/bin/foo``) and ancestors (``/jig``, ``/``) — any
        overlap can shadow or expose the enforcement artefacts."""

        reserved = {self.hook_bin_mount, self.policy_mount}
        for src, dst in self.extra_ro_binds:
            self._reject_reserved(dst, reserved, "extra_ro_binds", src)
        for src, dst in self.extra_rw_binds:
            self._reject_reserved(dst, reserved, "extra_rw_binds", src)
        for dst in self.hide_paths:
            self._reject_reserved(dst, reserved, "hide_paths", None)

    @staticmethod
    def _reject_reserved(
        dst: str,
        reserved: set[str],
        field_name: str,
        src: str | None,
    ) -> None:
        """Raise ``ValueError`` if ``dst`` overlaps any reserved mount.

        ``dst`` overlaps a reserved mount point when it equals it, is
        a descendant of it, or is an ancestor of it. Uses path-segment
        comparison so ``/jig/binned`` is not treated as being under
        ``/jig/bin``."""

        dst_parts = _normalise_mount_path(dst)
        for mount in reserved:
            mount_parts = _normalise_mount_path(mount)
            n = min(len(dst_parts), len(mount_parts))
            if dst_parts[:n] == mount_parts[:n]:
                location = f"(src={src!r})" if src is not None else ""
                raise ValueError(
                    f"{field_name} entry targets reserved mount {mount!r}: "
                    f"{dst!r} overlaps the capability-policy mount point "
                    f"{location}".rstrip()
                )

    def to_args(self) -> list[str]:
        """Build the bwrap argument list."""
        args: list[str] = [
            # Drop the orchestrator's environment. We --setenv exactly the
            # vars the agent needs below; everything else (OAuth tokens,
            # JIG_*, GIT_*, GH_TOKEN, ...) stays out of the sandbox.
            "--clearenv",
            # Full container filesystem, read-only
            "--ro-bind",
            "/",
            "/",
            # Agent's worktree, read-write
            "--bind",
            str(self.worktree_host_path),
            self.workspace,
            # /proc and /dev are separate mount points — --ro-bind / /
            # doesn't capture them.  Bind-mount from the parent instead of
            # mounting fresh (--proc /proc requires privileges Docker blocks).
            "--ro-bind",
            "/proc",
            "/proc",
            "--dev-bind",
            "/dev",
            "/dev",
            # Isolated temp
            "--tmpfs",
            "/tmp",
        ]

        # Docker volume mounts are separate mount points that
        # --ro-bind / / doesn't capture. Bind them explicitly,
        # READ-ONLY: a Bash-capable agent must not be able to mutate
        # the orchestrator's Claude state, plugins, or cached tokens.
        # Token refresh by the orchestrator happens out-of-band; an
        # in-agent refresh is rare and would fail closed (the agent
        # exits, the operator re-auths) rather than open.
        home = Path.home()
        claude_dir = home / ".claude"
        if claude_dir.is_dir():
            args.extend(["--ro-bind", str(claude_dir), str(claude_dir)])
        claude_json = home / ".claude.json"
        if claude_json.is_file():
            args.extend(["--ro-bind", str(claude_json), str(claude_json)])

        # Hide paths: tmpfs for directories, /dev/null bind for files.
        # bwrap requires --tmpfs targets to be directories; using it on a
        # file path (e.g. /home/jig/.gitconfig mounted as a Docker volume)
        # causes "Can't mkdir … Not a directory".
        for path in self.hide_paths:
            if Path(path).is_file():
                args.extend(["--bind", "/dev/null", path])
            else:
                args.extend(["--tmpfs", path])

        # Capability policy artefacts (Phase 5 Task G). Bind-mount
        # read-only: the hook scripts only read these; nothing in the
        # agent's sandbox should be able to rewrite its own ruleset or
        # the enforcement binaries. These come before ``extra_ro_binds``
        # so the reserved mounts appear first in the audit log;
        # ``__post_init__`` already rejects extra mounts that overlap
        # these destinations, so argument ordering is defence in depth
        # rather than the primary guarantee.
        if self.hook_bin_host_path is not None:
            args.extend(
                [
                    "--ro-bind",
                    str(self.hook_bin_host_path),
                    self.hook_bin_mount,
                ]
            )
        if self.policy_dir_host_path is not None:
            args.extend(
                [
                    "--ro-bind",
                    str(self.policy_dir_host_path),
                    self.policy_mount,
                ]
            )

        # Extra mounts
        for src, dst in self.extra_ro_binds:
            args.extend(["--ro-bind", src, dst])
        for src, dst in self.extra_rw_binds:
            args.extend(["--bind", src, dst])

        # Forward only the curated env-var allowlist into the sandbox.
        # ``--clearenv`` above wiped everything, so an explicit --setenv
        # is required for each var the agent legitimately needs.
        for key in self.passthrough_env_keys:
            val = os.environ.get(key)
            if val is not None:
                args.extend(["--setenv", key, val])
        for key, val in self.extra_setenv:
            args.extend(["--setenv", key, val])

        # Working directory + namespace isolation
        args.extend(
            [
                "--chdir",
                self.workspace,
                "--setenv",
                "PWD",
                self.workspace,
                "--unshare-pid",
                "--die-with-parent",
            ]
        )
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
