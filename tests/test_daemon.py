"""Tests for jig.daemon — background process management."""
import os
import time

from jig.daemon import daemon_paths, daemon_status, daemon_start, daemon_stop


def test_daemon_paths_returns_pid_and_socket_under_jig_run(tmp_path):
    paths = daemon_paths(tmp_path)
    assert paths.pid_file == tmp_path / ".jig" / "run" / "daemon.pid"
    assert paths.socket_addr_file == tmp_path / ".jig" / "run" / "daemon.addr"
    assert paths.stderr_log == tmp_path / ".jig" / "run" / "daemon.err"
    assert paths.run_dir == tmp_path / ".jig" / "run"


def test_daemon_paths_creates_run_dir_when_requested(tmp_path):
    paths = daemon_paths(tmp_path, ensure=True)
    assert paths.run_dir.is_dir()


def test_daemon_status_not_running_when_no_pid_file(tmp_path):
    status = daemon_status(tmp_path)
    assert status.running is False
    assert status.pid is None


def test_daemon_status_reports_pid_when_pid_file_exists_and_alive(tmp_path):
    paths = daemon_paths(tmp_path, ensure=True)
    paths.pid_file.write_text(str(os.getpid()))
    status = daemon_status(tmp_path)
    assert status.running is True
    assert status.pid == os.getpid()


def test_daemon_status_reports_stale_when_pid_file_exists_but_dead(tmp_path):
    paths = daemon_paths(tmp_path, ensure=True)
    # PID 99999999 is virtually guaranteed not to exist.
    paths.pid_file.write_text("99999999")
    status = daemon_status(tmp_path)
    assert status.running is False
    assert status.stale is True


def test_daemon_start_writes_pid_file_and_can_be_stopped(tmp_path):
    """Smoke test: start a no-op daemon (just `sleep 60`), verify the
    PID file is written and the process is alive, then stop it."""
    started = daemon_start(
        tmp_path,
        # _command_override is a test-only seam: instead of running the
        # real orchestrator, the daemon forks `sleep 60` as a stand-in.
        _command_override=["sleep", "60"],
    )
    try:
        time.sleep(0.5)
        status = daemon_status(tmp_path)
        assert status.running is True
        assert status.pid == started.pid
    finally:
        daemon_stop(tmp_path)
        time.sleep(0.5)

    assert daemon_status(tmp_path).running is False


def test_daemon_start_raises_when_child_dies_immediately(tmp_path):
    """If the spawned child exits during the startup-poll window, the
    function cleans up files and raises with the stderr tail."""
    import pytest

    # `false` exits immediately with rc=1; bash echoes a marker to stderr.
    with pytest.raises(RuntimeError, match=r"daemon exited immediately"):
        daemon_start(
            tmp_path,
            _command_override=["bash", "-c", "echo 'BOOM' >&2; exit 7"],
        )
    paths = daemon_paths(tmp_path)
    # Files cleaned up so a retry can write fresh ones
    assert not paths.pid_file.exists()
    assert not paths.socket_addr_file.exists()
    # Stderr was captured
    assert paths.stderr_log.is_file()
    assert "BOOM" in paths.stderr_log.read_text()


def test_daemon_start_rejects_uninitialized_project(tmp_path):
    """Pre-flight: refuse to fork if .jig/config.yaml is absent."""
    import pytest

    with pytest.raises(RuntimeError, match=r"not a jig project"):
        daemon_start(tmp_path)


def test_daemon_status_surfaces_last_error_for_stale_pid_file(tmp_path):
    """When the PID is dead, status returns the last line of daemon.err."""
    paths = daemon_paths(tmp_path, ensure=True)
    paths.pid_file.write_text("99999999")
    paths.stderr_log.write_text("first line\nimportant final error\n")
    status = daemon_status(tmp_path)
    assert status.running is False
    assert status.stale is True
    assert status.last_error == "important final error"
