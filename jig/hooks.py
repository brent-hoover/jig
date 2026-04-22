"""Human-side git hooks for jig (Phase 5 Task I).

Per docs/superpowers/specs/2026-04-22-jig-hooks-design.md. This module
owns three responsibilities:

* Hook script templates (bash trampolines) and sentinel detection for
  identifying jig-managed hooks on disk.
* Install / uninstall / status operations with backup handling so we
  never stomp a user's existing hooks.
* Per-stage runners that mirror a subset of the agent harness's
  check catalog: pre-commit runs all required scripted checks;
  pre-push runs the current phase's scripted checks (or falls back
  to Project.hooks.pre_push_command); commit-msg enforces conventional
  commits.

The runners intentionally do NOT persist `CheckResult` records —
hooks are a dev-loop parity layer, not a record of truth. The
harness remains canonical.
"""

from __future__ import annotations

from pathlib import Path

# The three git hook names jig installs. Fixed for v1 — per-hook
# install flags are YAGNI (see spec §Non-Goals).
HOOK_NAMES: tuple[str, ...] = ("pre-commit", "pre-push", "commit-msg")

# Literal second-line string that marks a hook file as ours. We control
# the bytes we write, so a byte-exact comparison on line 2 is enough.
SENTINEL_LINE = (
    "# jig-managed hook — safe to remove via 'jig hooks uninstall'"
)


def _build_script(stage: str, *, forward: str) -> str:
    """Construct a hook trampoline for a single stage.

    ``forward`` is the argv tail passed to ``jig hooks run <stage>``:
    empty for pre-commit, ``"$@"`` for pre-push (git pipes ref info on
    stdin + passes remote/url as argv), and ``"$1"`` for commit-msg
    (path to the commit message file).
    """
    tail = f" {forward}" if forward else ""
    return (
        "#!/usr/bin/env bash\n"
        f"{SENTINEL_LINE}\n"
        f"# stage: {stage}\n"
        "set -e\n"
        "\n"
        "if ! command -v jig >/dev/null 2>&1; then\n"
        '  echo "jig hook: '"'"'jig'"'"' command not found on PATH." >&2\n'
        '  echo "Install jig or run '"'"'jig hooks uninstall'"'"' to remove this hook." >&2\n'
        "  exit 1\n"
        "fi\n"
        "\n"
        f'exec jig hooks run {stage}{tail}\n'
    )


HOOK_SCRIPTS: dict[str, str] = {
    "pre-commit": _build_script("pre-commit", forward=""),
    "pre-push": _build_script("pre-push", forward='"$@"'),
    "commit-msg": _build_script("commit-msg", forward='"$1"'),
}


def _is_jig_managed(hook_path: Path) -> bool:
    """True when ``hook_path`` exists and line 2 matches the sentinel.

    Strictly byte-compares the second line. Files shorter than two
    lines, missing files, or foreign hooks all return False.
    """
    try:
        text = hook_path.read_text()
    except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
        return False
    lines = text.splitlines()
    if len(lines) < 2:
        return False
    return lines[1] == SENTINEL_LINE


__all__ = [
    "HOOK_NAMES",
    "HOOK_SCRIPTS",
    "SENTINEL_LINE",
    "_is_jig_managed",
]
