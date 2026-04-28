"""Background daemon process management for jig.

The daemon is a long-running process that hosts the orchestrator,
the agent dispatcher, and the WebSocket server. The TUI is a client
that connects to it. Closing the TUI does not stop the daemon —
agents in flight finish their work.

PID and socket-address files live under ``.jig/run/`` so the TUI can
discover a running daemon for the current project.

See ``docs/textual-tui/design.md`` §"Daemon lifecycle".
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class DaemonPaths:
    run_dir: Path
    pid_file: Path
    socket_addr_file: Path
    stderr_log: Path


def daemon_paths(project_path: Path, *, ensure: bool = False) -> DaemonPaths:
    """Compute the daemon's runtime file paths under ``.jig/run/``.

    When ``ensure`` is True, create the directory if missing.
    """
    run_dir = project_path / ".jig" / "run"
    if ensure:
        run_dir.mkdir(parents=True, exist_ok=True)
    return DaemonPaths(
        run_dir=run_dir,
        pid_file=run_dir / "daemon.pid",
        socket_addr_file=run_dir / "daemon.addr",
        stderr_log=run_dir / "daemon.err",
    )


@dataclass(frozen=True)
class DaemonStatus:
    running: bool
    pid: int | None = None
    stale: bool = False  # True when PID file exists but no live process
    last_error: str | None = None  # tail of daemon.err when stale


def daemon_status(project_path: Path) -> DaemonStatus:
    """Inspect the daemon's run-state via its PID file.

    Returns ``running=True`` only if the PID file exists AND the
    process is alive. ``stale=True`` indicates a leftover PID file
    pointing at a dead process — caller should clean it up before
    starting a new daemon. When stale, ``last_error`` carries the last
    line of ``daemon.err`` when present.
    """
    paths = daemon_paths(project_path)
    if not paths.pid_file.is_file():
        return DaemonStatus(running=False)
    try:
        pid = int(paths.pid_file.read_text().strip())
    except ValueError:
        return DaemonStatus(running=False, stale=True, last_error=_tail_err(paths))
    if _process_alive(pid):
        return DaemonStatus(running=True, pid=pid)
    return DaemonStatus(running=False, stale=True, last_error=_tail_err(paths))


def _tail_err(paths: DaemonPaths) -> str | None:
    """Read the last non-empty line of daemon.err, if present."""
    if not paths.stderr_log.is_file():
        return None
    text = paths.stderr_log.read_text(errors="replace").strip()
    if not text:
        return None
    return text.splitlines()[-1]


def _process_alive(pid: int) -> bool:
    """True if ``pid`` is a live process this user can signal."""
    try:
        # Signal 0 just checks for existence; doesn't actually deliver.
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — treat as alive.
        return True
    return True


@dataclass(frozen=True)
class DaemonStartResult:
    pid: int
    addr: str  # e.g. "ws://127.0.0.1:9100"


class DaemonAlreadyRunning(RuntimeError):
    """Raised when daemon_start finds an existing live daemon."""


def daemon_start(
    project_path: Path,
    *,
    ws_port: int = 9100,
    _command_override: Sequence[str] | None = None,
) -> DaemonStartResult:
    """Fork a background daemon process for this project.

    The daemon runs ``jig daemon serve`` (an internal subcommand that
    actually hosts the orchestrator). PID + socket address are written
    to ``.jig/run/`` so the TUI can find them.

    ``_command_override`` is a test-only seam — production callers
    leave it None.

    Raises ``DaemonAlreadyRunning`` if a live daemon already exists.
    """
    existing = daemon_status(project_path)
    if existing.running:
        raise DaemonAlreadyRunning(
            f"daemon already running (pid={existing.pid}); use "
            "`jig daemon stop` first"
        )
    if existing.stale:
        # Clean up the stale PID file so the new daemon can write its own.
        daemon_paths(project_path).pid_file.unlink(missing_ok=True)

    paths = daemon_paths(project_path, ensure=True)
    cmd = list(_command_override) if _command_override else [
        "jig", "daemon", "serve",
        "--path", str(project_path),
        "--ws-port", str(ws_port),
    ]
    # Detach: stdin from /dev/null, stdout to /dev/null, stderr to a log
    # file so silent crashes are debuggable. start_new_session=True so the
    # child survives the parent shell exit.
    stderr_fd = paths.stderr_log.open("ab")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_fd,
            start_new_session=True,
        )
    finally:
        stderr_fd.close()
    paths.pid_file.write_text(str(proc.pid))
    addr = f"ws://127.0.0.1:{ws_port}"
    paths.socket_addr_file.write_text(addr)

    # Brief poll to surface immediate-death cases (e.g. project not
    # initialized, port collision). 1.5s is enough for the orchestrator
    # to bind the WS port; we don't want to make `daemon start` slow.
    deadline = time.time() + 1.5
    while time.time() < deadline:
        if proc.poll() is not None:
            # Child exited; clean up files and surface the error tail.
            paths.pid_file.unlink(missing_ok=True)
            paths.socket_addr_file.unlink(missing_ok=True)
            tail = ""
            if paths.stderr_log.is_file():
                err_text = paths.stderr_log.read_text(errors="replace").strip()
                if err_text:
                    tail = "\n  " + err_text.splitlines()[-1]
            raise RuntimeError(
                f"daemon exited immediately (rc={proc.returncode}); "
                f"see {paths.stderr_log}{tail}"
            )
        time.sleep(0.05)
    return DaemonStartResult(pid=proc.pid, addr=addr)


def daemon_stop(project_path: Path, *, timeout: float = 5.0) -> bool:
    """Stop the daemon if running. Returns True if a running daemon
    was stopped, False if no daemon was running. Sends SIGTERM, waits
    up to ``timeout`` seconds, then SIGKILL."""
    status = daemon_status(project_path)
    if not status.running or status.pid is None:
        # Clean up stale PID file if present.
        daemon_paths(project_path).pid_file.unlink(missing_ok=True)
        return False
    try:
        os.kill(status.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _process_alive(status.pid):
            break
        time.sleep(0.1)
    else:
        try:
            os.kill(status.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    daemon_paths(project_path).pid_file.unlink(missing_ok=True)
    daemon_paths(project_path).socket_addr_file.unlink(missing_ok=True)
    return True
