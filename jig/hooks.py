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

import asyncio
import shutil
import subprocess
from pathlib import Path

import click
import yaml

from jig.checks import CheckSeverity, ScriptedCheck, load_check_catalog
from jig.config import load_config
from jig.persistence import load_workflow
from jig.phase import current_phase_index
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore

# The three git hook names jig installs. Fixed for v1 — per-hook
# install flags are YAGNI (see spec §Non-Goals).
HOOK_NAMES: tuple[str, ...] = ("pre-commit", "pre-push", "commit-msg")

# Literal second-line string that marks a hook file as ours. We control
# the bytes we write, so a byte-exact comparison on line 2 is enough.
# The em-dash is U+2014, not ASCII `--`. Autocorrect would silently
# break recognition of previously-installed hooks — leave it alone.
SENTINEL_LINE = "# jig-managed hook — safe to remove via 'jig hooks uninstall'"


def _build_script(stage: str, *, forward: str) -> str:
    """Construct a hook trampoline for a single stage.

    ``forward`` is the argv tail passed to ``jig hooks run <stage>``:
    empty for pre-commit, ``"$@"`` for pre-push (git pipes ref info on
    stdin + passes remote/url as argv), and ``"$1"`` for commit-msg
    (path to the commit message file).
    """
    tail = f" {forward}" if forward else ""
    return f"""\
#!/usr/bin/env bash
{SENTINEL_LINE}
# stage: {stage}
set -e

if ! command -v jig >/dev/null 2>&1; then
  echo "jig hook: 'jig' command not found on PATH." >&2
  echo "Install jig or run 'jig hooks uninstall' to remove this hook." >&2
  exit 1
fi

exec jig hooks run {stage}{tail}
"""


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
    except (FileNotFoundError, IsADirectoryError, PermissionError, UnicodeDecodeError):
        return False
    lines = text.splitlines()
    if len(lines) < 2:
        return False
    return lines[1] == SENTINEL_LINE


def _git_common_dir(cwd: Path) -> Path:
    """Return the absolute path to the git common dir.

    Works from the main repo or from any worktree. Raises
    ``RuntimeError`` if ``cwd`` is not inside a git repository at all.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(f"{cwd} is not inside a git repository") from exc
    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError(f"{cwd} is not inside a git repository")
    p = Path(raw)
    if not p.is_absolute():
        p = (cwd / p).resolve()
    return p


def _resolve_ticket_worktree(cwd: Path) -> str | None:
    """If ``cwd`` is inside a jig ticket worktree, return its ticket id.

    A ticket worktree is exactly ``<project>/.jig/worktrees/<ticket_id>/``.
    Returns None for any other layout — including directories that
    contain a ``worktrees`` folder but not under ``.jig/``.
    """
    cwd = cwd.resolve()
    parent = cwd.parent
    grandparent = parent.parent
    if parent.name != "worktrees":
        return None
    if grandparent.name != ".jig":
        return None
    return cwd.name


class HookInstallError(RuntimeError):
    """Raised when install_hooks refuses a clobber."""


def install_hooks(project_path: Path, *, force: bool = False) -> list[str]:
    """Install all three jig hooks under the project's git common dir.

    Returns a list of human-readable status lines (one per hook) so
    the CLI can echo them. Raises ``HookInstallError`` if a non-jig
    hook would overwrite an existing ``.jig-backup`` and ``force`` is
    False.
    """
    common = _git_common_dir(project_path)
    hooks_dir = common / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    report: list[str] = []
    for name in HOOK_NAMES:
        target = hooks_dir / name
        backup = hooks_dir / f"{name}.jig-backup"
        script = HOOK_SCRIPTS[name]

        if not target.exists():
            _write_hook(target, script)
            report.append(f"installed {name}")
            continue

        if _is_jig_managed(target):
            _write_hook(target, script)
            report.append(f"refreshed {name}")
            continue

        # Target exists and isn't ours.
        if backup.exists() and not force:
            raise HookInstallError(
                f"refusing to overwrite {target}: backup already exists at "
                f"{backup}. Re-run with --force to replace the backup."
            )
        # Preserve original mode bits on the backup file.
        shutil.move(str(target), str(backup))
        _write_hook(target, script)
        report.append(f"installed {name} (existing hook backed up to .jig-backup)")
    return report


def _write_hook(target: Path, script: str) -> None:
    """Atomically-ish write the hook script and chmod 0755."""
    target.write_text(script)
    target.chmod(0o755)


def uninstall_hooks(project_path: Path) -> list[str]:
    """Remove jig-managed hooks and restore any backups.

    Never errors on foreign hooks; reports them instead. Returns a
    list of human-readable status lines.
    """
    common = _git_common_dir(project_path)
    hooks_dir = common / "hooks"
    report: list[str] = []

    for name in HOOK_NAMES:
        target = hooks_dir / name
        backup = hooks_dir / f"{name}.jig-backup"

        if not target.exists():
            report.append(f"not installed: {name}")
            continue
        if not _is_jig_managed(target):
            report.append(f"skipped: {name} (not jig-managed)")
            continue
        target.unlink()
        if backup.exists():
            shutil.move(str(backup), str(target))
            report.append(f"uninstalled {name}, restored original from .jig-backup")
        else:
            report.append(f"uninstalled {name}")
    return report


def hook_status(project_path: Path) -> list[str]:
    """Return one status line per hook for ``jig hooks status``.

    Purely informational — never raises on foreign hooks or missing
    files. The CLI prints these verbatim.
    """
    common = _git_common_dir(project_path)
    hooks_dir = common / "hooks"
    lines: list[str] = []
    width = max(len(n) for n in HOOK_NAMES) + 1
    for name in HOOK_NAMES:
        target = hooks_dir / name
        label = f"{name}:".ljust(width + 1)
        if not target.exists():
            lines.append(f"{label} not installed")
        elif _is_jig_managed(target):
            lines.append(f"{label} installed (jig-managed)")
        else:
            lines.append(
                f"{label} exists but not jig-managed — "
                "'jig hooks install --force' to back up and replace"
            )
    return lines


async def _run_scripted(name: str, check: ScriptedCheck, cwd: Path) -> tuple[bool, str]:
    """Run one scripted check, stream output, return (passed, reason).

    Does NOT persist a CheckResult — hooks are ephemeral. ``reason``
    is the terse failure label printed in the summary block (empty
    when passing). Commands run under ``/bin/sh -c`` in ``cwd`` so the
    same shell-aware strings used by the catalog's ScriptedRunner
    work identically.
    """
    click.echo(f"\u25b6 {name}: {check.command}")
    working_dir = (cwd / check.working_dir).resolve()
    # stdout/stderr default to None → subprocess inherits the parent's
    # real fds so the developer sees live output. We don't forward
    # sys.stdout/sys.stderr explicitly because test harnesses (pytest
    # capsys) replace those with objects lacking a ``.fileno()``, which
    # would break subprocess spawning.
    try:
        proc = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-c",
            check.command,
            cwd=str(working_dir),
        )
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        click.echo(f"\u2717 {name} spawn failed: {exc}", err=True)
        return False, f"{name}: spawn failed"

    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout=check.timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        return False, f"{name}: timed out after {check.timeout_s}s"

    if returncode == 0:
        click.echo(f"\u2713 {name}")
        return True, ""
    return False, f"{name}: exit {returncode}"


def _print_failure_summary(failures: list[str]) -> None:
    # Print to stdout so test capsys.readouterr().out captures it.
    click.echo("")
    click.echo("jig hook: required checks failed")
    for f in failures:
        click.echo(f"  - {f}")
    click.echo("Fix the failures above, or use 'git commit --no-verify' to bypass.")


async def run_pre_commit(project_path: Path) -> int:
    """Execute all required scripted checks in the catalog.

    Returns the exit code the hook should terminate with.
    """
    catalog = load_check_catalog(project_path)
    required_scripted = [
        (name, check)
        for name, check in catalog.root.items()
        if isinstance(check, ScriptedCheck) and check.severity is CheckSeverity.REQUIRED
    ]
    if not required_scripted:
        click.echo("jig pre-commit: no required scripted checks; skipping")
        return 0

    failures: list[str] = []
    for name, check in required_scripted:
        passed, reason = await _run_scripted(name, check, project_path)
        if not passed:
            failures.append(reason)

    if failures:
        _print_failure_summary(failures)
        return 1
    return 0


def _project_root_from_worktree(cwd: Path) -> Path:
    """Given a cwd inside a ticket worktree, return the project root.

    Layout: ``<project>/.jig/worktrees/<ticket_id>/`` → ``<project>``.
    """
    return cwd.resolve().parent.parent.parent


async def _run_pre_push_in_worktree(worktree: Path, ticket_id: str) -> int | None:
    """Run the current phase's scripted checks from inside a ticket worktree.

    Returns:
        * ``0`` — all scripted checks for the current phase passed
          (or the phase has no scripted checks, or every phase has
          already completed).
        * ``1`` — at least one scripted check failed.
        * ``None`` — the config, ticket, or workflow couldn't be
          resolved; the caller should fall through to the fallback
          command.
    """
    project_root = _project_root_from_worktree(worktree)
    # Probe the project config early — a missing config.yaml means the
    # worktree parent isn't a real jig project, so fall through to the
    # fallback. We don't need the parsed Config itself here.
    try:
        load_config(project_root)
    except FileNotFoundError:
        return None

    tickets = TicketStore(project_root / ".jig" / "store" / "tickets.jsonl")
    try:
        await tickets.load()
    except (FileNotFoundError, ValueError):
        return None
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        return None

    # NB: config.workflows is a ``WorkflowsSection`` (a resolution
    # policy, not a catalog of WorkflowConfigs). The catalog lookup is
    # ``jig.persistence.load_workflow`` with the project → shipped-default
    # fallback. Missing/malformed workflow YAML is treated as "no
    # phase-aware check list available" and we skip rather than error
    # out the push.
    try:
        workflow = load_workflow(project_root, ticket.workflow)
    except (FileNotFoundError, yaml.YAMLError):
        click.echo(f"jig pre-push: workflow {ticket.workflow!r} not found; skipping")
        return 0

    threads = ThreadStore(project_root / ".jig" / "store" / "threads.jsonl")
    await threads.load()
    idx = await current_phase_index(threads, ticket_id, workflow)
    if idx >= len(workflow.phases):
        click.echo("jig pre-push: all phases complete; skipping")
        return 0
    phase = workflow.phases[idx]

    catalog = load_check_catalog(project_root)
    scripted_names = [
        n for n in phase.automated_checks if isinstance(catalog.get(n), ScriptedCheck)
    ]
    if not scripted_names:
        click.echo(
            f"jig pre-push: phase {phase.name!r} has no scripted checks; skipping"
        )
        return 0

    failures: list[str] = []
    for name in scripted_names:
        check = catalog.get(name)
        assert isinstance(check, ScriptedCheck)
        passed, reason = await _run_scripted(name, check, worktree)
        if not passed:
            failures.append(reason)
    if failures:
        _print_failure_summary(failures)
        return 1
    return 0


async def _run_pre_push_fallback(project_root: Path) -> int:
    """Run the developer's configured pre-push command, or skip."""
    try:
        config = load_config(project_root)
    except FileNotFoundError:
        click.echo("jig pre-push: no .jig/config.yaml; skipping")
        return 0
    command = config.project.hooks.pre_push_command
    if not command:
        click.echo("jig pre-push: hooks.pre_push_command not set; skipping")
        return 0
    click.echo(f"\u25b6 pre-push: {command}")
    # Like ``_run_scripted``: inherit the parent's real stdout/stderr fds
    # rather than forwarding sys.stdout/sys.stderr, which pytest capsys
    # replaces with objects lacking a ``.fileno()``.
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        command,
        cwd=str(project_root),
    )
    returncode = await proc.wait()
    if returncode == 0:
        return 0
    _print_failure_summary([f"pre_push_command: exit {returncode}"])
    return 1


async def run_pre_push(cwd: Path) -> int:
    """Phase-aware in a ticket worktree; falls back to the project command.

    Returns the exit code the hook should terminate with.
    """
    ticket_id = _resolve_ticket_worktree(cwd)
    if ticket_id is not None:
        rc = await _run_pre_push_in_worktree(cwd, ticket_id)
        if rc is not None:
            return rc
    # Fallback path: cwd is either outside a jig worktree, or we couldn't
    # resolve ticket/workflow state — behave as the outside-worktree case.
    project_root = _project_root_from_worktree(cwd) if ticket_id else cwd
    return await _run_pre_push_fallback(project_root)


__all__ = [
    "HOOK_NAMES",
    "HOOK_SCRIPTS",
    "SENTINEL_LINE",
    "HookInstallError",
    "_git_common_dir",
    "_is_jig_managed",
    "_resolve_ticket_worktree",
    "_write_hook",
    "hook_status",
    "install_hooks",
    "run_pre_commit",
    "run_pre_push",
    "uninstall_hooks",
]
