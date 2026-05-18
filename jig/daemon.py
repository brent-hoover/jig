"""Background daemon process management for jig.

The daemon is a long-running process that hosts the orchestrator
and the WebSocket server. The TUI is a client
that connects to it. Closing the TUI does not stop the daemon —
agents in flight finish their work.

PID and socket-address files live under ``.jig/run/`` so the TUI can
discover a running daemon for the current project.

See ``docs/textual-tui/design.md`` §"Daemon lifecycle".
"""

from __future__ import annotations

import os
import signal
import socket
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
    container_file: Path  # stores container ID when daemon runs in Docker


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
        container_file=run_dir / "daemon.container",
    )


@dataclass(frozen=True)
class DaemonStatus:
    running: bool
    pid: int | None = None
    container_id: str | None = None  # set when running in Docker
    kind: str | None = None  # "host" or "docker" when running
    stale: bool = False  # True when state file exists but no live process/container
    last_error: str | None = None  # tail of daemon.err when stale


def daemon_status(project_path: Path) -> DaemonStatus:
    """Inspect the daemon's run-state via its PID or container file.

    Docker mode takes precedence — its file is only present when
    started in docker mode.

    Returns ``running=True`` only if the process / container is alive.
    ``stale=True`` indicates a leftover state file pointing at a dead
    process or container — caller should clean it up before starting
    a new daemon.
    """
    from jig.container import is_in_container

    # When we're running inside the container we ARE the daemon — Docker CLI
    # is not available and the container file is meaningless from this side.
    if is_in_container():
        return DaemonStatus(running=True, kind="docker")

    paths = daemon_paths(project_path)

    # Docker mode: container_file present
    if paths.container_file.is_file():
        cid = paths.container_file.read_text().strip()
        if not cid:
            return DaemonStatus(running=False, stale=True, last_error=_tail_err(paths))
        from jig.container import container_alive

        if container_alive(cid):
            return DaemonStatus(running=True, container_id=cid, kind="docker")
        return DaemonStatus(
            running=False,
            stale=True,
            container_id=cid,
            last_error=_tail_err(paths),
        )

    # Host mode: pid_file present
    if paths.pid_file.is_file():
        try:
            pid = int(paths.pid_file.read_text().strip())
        except ValueError:
            return DaemonStatus(running=False, stale=True, last_error=_tail_err(paths))
        if _process_alive(pid):
            return DaemonStatus(running=True, pid=pid, kind="host")
        return DaemonStatus(running=False, stale=True, last_error=_tail_err(paths))

    # No PID file — check for an orphaned daemon (process still holds the
    # port but its PID file was cleaned up).  Only check when we have a
    # recorded address; without it we cannot distinguish a cleanly-stopped
    # daemon from one that crashed and left a stray process.
    if paths.socket_addr_file.is_file():
        try:
            ws_port = int(paths.socket_addr_file.read_text().strip().rsplit(":", 1)[-1])
        except (ValueError, IndexError):
            ws_port = 19100
        orphan_pid = _pid_on_port(ws_port)
        if orphan_pid is not None:
            return DaemonStatus(
                running=True, pid=orphan_pid, kind="host", last_error=_tail_err(paths)
            )

    return DaemonStatus(running=False)


def _tail_err(paths: DaemonPaths) -> str | None:
    """Read the last non-empty line of daemon.err, if present."""
    if not paths.stderr_log.is_file():
        return None
    text = paths.stderr_log.read_text(errors="replace").strip()
    if not text:
        return None
    return text.splitlines()[-1]


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _allocate_ws_port(preferred: int = 19100) -> int:
    """Return ``preferred`` if free, else an OS-assigned ephemeral port.

    Lets multiple projects auto-start daemons concurrently — the first
    gets 19100; subsequent projects get their own ephemeral ports.
    Resolved port is written to ``.jig/run/daemon.addr`` so the TUI
    discovers it without ambient knowledge of which port to use.
    """
    if not _port_in_use(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pid_on_port(port: int) -> int | None:
    """Return the PID of the process listening on ``port``, or None.

    Uses ``lsof`` (available on macOS and Linux) to find the process.
    Returns None if lsof is unavailable, the port is free, or resolution fails.
    """
    if not _port_in_use(port):
        return None
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-t", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return int(result.stdout.strip().splitlines()[0])
    except (subprocess.SubprocessError, FileNotFoundError, ValueError, OSError):
        return None


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
    addr: str  # e.g. "ws://127.0.0.1:19100"
    container_id: str | None = None
    # Container IDs (12-char short) removed prior to launch because they
    # were orphans from a previous run. Empty in normal cases.
    orphans_removed: tuple[str, ...] = ()


class DaemonAlreadyRunning(RuntimeError):
    """Raised when daemon_start finds an existing live daemon."""


def daemon_start(
    project_path: Path,
    *,
    ws_port: int | None = None,
    docker: bool = False,
    _command_override: Sequence[str] | None = None,
) -> DaemonStartResult:
    """Fork a background daemon process for this project.

    In host mode the daemon runs ``jig daemon serve`` as a detached
    subprocess. In docker mode it launches the orchestrator in a
    detached container. PID / container ID + socket address are written
    to ``.jig/run/`` so the TUI can find them.

    ``ws_port=None`` (default) picks 19100 if free, else an ephemeral
    port — letting multiple projects run daemons concurrently. Pass an
    int to override.

    ``_command_override`` is a test-only seam — production callers
    leave it None (only honoured in host mode).

    Raises ``DaemonAlreadyRunning`` if a live daemon already exists.
    Raises ``RuntimeError`` if the project is not initialized (host
    mode only) or if Docker is unavailable / image not built (docker mode).
    """
    if ws_port is None:
        ws_port = _allocate_ws_port()
    existing = daemon_status(project_path)
    if existing.running:
        pid_or_cid = (
            f"container={existing.container_id[:12]}"
            if existing.container_id
            else f"pid={existing.pid}"
        )
        raise DaemonAlreadyRunning(
            f"daemon already running ({pid_or_cid}); use `jig daemon stop` first"
        )
    if existing.stale:
        # Clean up stale state for whichever mode it was.
        paths = daemon_paths(project_path)
        paths.pid_file.unlink(missing_ok=True)
        paths.container_file.unlink(missing_ok=True)

    paths = daemon_paths(project_path, ensure=True)

    # ------------------------------------------------------------------ docker
    if docker:
        from jig.container import (
            docker_available,
            image_exists,
            run_detached_container,
            container_alive,
            find_project_containers,
            force_remove_container,
        )

        if not docker_available():
            raise RuntimeError(
                "docker not available on PATH. Install Docker or omit --docker."
            )
        if not image_exists():
            raise RuntimeError("jig Docker image not built. Run `jig build` first.")
        # daemon_status already determined no live daemon, so any container
        # labeled with this project is an orphan from a prior run whose
        # state files were lost or never written. Remove before launch so
        # the new container can bind the port.
        orphans_removed: list[str] = []
        for cid in find_project_containers(project_path):
            if force_remove_container(cid):
                orphans_removed.append(cid[:12])
        try:
            container_id = run_detached_container(project_path, ws_port, verbose=False)
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"failed to launch container: {exc}") from exc
        paths.container_file.write_text(container_id)
        addr = f"ws://127.0.0.1:{ws_port}"
        paths.socket_addr_file.write_text(addr)
        # Brief poll: did the container die immediately?
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if not container_alive(container_id):
                paths.container_file.unlink(missing_ok=True)
                paths.socket_addr_file.unlink(missing_ok=True)
                # Capture container logs for diagnosis.
                try:
                    log_result = subprocess.run(
                        ["docker", "logs", container_id],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    err_excerpt = (log_result.stderr or log_result.stdout).strip()
                    paths.stderr_log.write_text(err_excerpt)
                except Exception:
                    err_excerpt = ""
                tail = ""
                if err_excerpt:
                    tail = "\n  " + err_excerpt.splitlines()[-1]
                raise RuntimeError(
                    f"container exited immediately (id={container_id[:12]}); "
                    f"see {paths.stderr_log}{tail}"
                )
            time.sleep(0.1)
        return DaemonStartResult(
            pid=0,
            addr=addr,
            container_id=container_id,
            orphans_removed=tuple(orphans_removed),
        )

    # ------------------------------------------------------------------- host
    cmd = (
        list(_command_override)
        if _command_override
        else [
            "jig",
            "daemon",
            "serve",
            "--path",
            str(project_path),
            "--ws-port",
            str(ws_port),
        ]
    )
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
            # Child exited — only clean up the files we wrote. If the pid
            # file was overwritten by a concurrently started daemon, don't
            # delete it (that would orphan the other daemon from status checks).
            try:
                written = int(paths.pid_file.read_text().strip())
                if written == proc.pid:
                    paths.pid_file.unlink(missing_ok=True)
            except (ValueError, OSError):
                paths.pid_file.unlink(missing_ok=True)
            paths.socket_addr_file.unlink(missing_ok=True)
            err_text = ""
            if paths.stderr_log.is_file():
                err_text = paths.stderr_log.read_text(errors="replace").strip()
            tail = ("\n  " + err_text.splitlines()[-1]) if err_text else ""
            # If the port is still occupied after the child died, something
            # else already owns it — treat as a running daemon rather than
            # surfacing a confusing EADDRINUSE error.
            if _port_in_use(ws_port):
                raise DaemonAlreadyRunning(
                    f"port {ws_port} already in use — daemon may already be running"
                )
            raise RuntimeError(
                f"daemon exited immediately (rc={proc.returncode}); "
                f"see {paths.stderr_log}{tail}"
            )
        time.sleep(0.05)
    return DaemonStartResult(pid=proc.pid, addr=addr)


def daemon_stop(project_path: Path, *, timeout: float = 5.0) -> bool:
    """Stop the daemon if running. Returns True if a running daemon
    was stopped, False if no daemon was running.

    Host mode: SIGTERM → wait → SIGKILL.
    Docker mode: ``docker stop``.
    """
    status = daemon_status(project_path)
    paths = daemon_paths(project_path)

    if not status.running:
        # Clean up any stale files from either mode.
        paths.pid_file.unlink(missing_ok=True)
        paths.container_file.unlink(missing_ok=True)
        return False

    if status.kind == "docker" and status.container_id:
        from jig.container import stop_detached_container

        stop_detached_container(status.container_id, timeout=int(timeout))
    elif status.kind == "host" and status.pid is not None:
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
        # Wait for the process to fully exit and release the port. SIGKILL
        # delivery is nearly instant but the kernel may still have the port
        # in TIME_WAIT for a brief moment after the process table entry is
        # gone. Waiting here avoids "port in use" errors on immediate restart.
        kill_deadline = time.time() + 3.0
        while time.time() < kill_deadline:
            if not _process_alive(status.pid):
                break
            time.sleep(0.05)

    paths.pid_file.unlink(missing_ok=True)
    paths.container_file.unlink(missing_ok=True)
    paths.socket_addr_file.unlink(missing_ok=True)
    return True
