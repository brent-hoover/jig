"""Tests for jig.daemon — background process management."""
import os
import socket
import time

from jig.daemon import (
    _allocate_ws_port,
    daemon_paths,
    daemon_status,
    daemon_start,
    daemon_stop,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_daemon_paths_returns_pid_and_socket_under_jig_run(tmp_path):
    paths = daemon_paths(tmp_path)
    assert paths.pid_file == tmp_path / ".jig" / "run" / "daemon.pid"
    assert paths.socket_addr_file == tmp_path / ".jig" / "run" / "daemon.addr"
    assert paths.stderr_log == tmp_path / ".jig" / "run" / "daemon.err"
    assert paths.container_file == tmp_path / ".jig" / "run" / "daemon.container"
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
    # Use a free ephemeral port so the _port_in_use check doesn't false-positive
    # against any real daemon running on the default port during tests.
    with pytest.raises(RuntimeError, match=r"daemon exited immediately"):
        daemon_start(
            tmp_path,
            ws_port=_free_port(),
            _command_override=["bash", "-c", "echo 'BOOM' >&2; exit 7"],
        )
    paths = daemon_paths(tmp_path)
    # Files cleaned up so a retry can write fresh ones
    assert not paths.pid_file.exists()
    assert not paths.socket_addr_file.exists()
    # Stderr was captured
    assert paths.stderr_log.is_file()
    assert "BOOM" in paths.stderr_log.read_text()


def test_daemon_start_succeeds_in_uninitialized_directory(tmp_path):
    """Pre-flight check removed: daemon now starts in unconfigured mode."""
    started = daemon_start(
        tmp_path,
        _command_override=["sleep", "60"],
    )
    try:
        time.sleep(0.5)
        assert daemon_status(tmp_path).running is True
        assert daemon_status(tmp_path).pid == started.pid
    finally:
        daemon_stop(tmp_path)


def test_daemon_status_surfaces_last_error_for_stale_pid_file(tmp_path):
    """When the PID is dead, status returns the last line of daemon.err."""
    paths = daemon_paths(tmp_path, ensure=True)
    paths.pid_file.write_text("99999999")
    paths.stderr_log.write_text("first line\nimportant final error\n")
    status = daemon_status(tmp_path)
    assert status.running is False
    assert status.stale is True
    assert status.last_error == "important final error"


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------


def test_allocate_ws_port_returns_preferred_when_free():
    """When the preferred port is free, allocate uses it as-is."""
    port = _free_port()  # known free at call time
    assert _allocate_ws_port(preferred=port) == port


def test_allocate_ws_port_falls_back_when_preferred_busy():
    """When the preferred port is bound, allocate returns an ephemeral
    port distinct from it — so two projects can run daemons concurrently."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        busy_port = held.getsockname()[1]
        chosen = _allocate_ws_port(preferred=busy_port)
        assert chosen != busy_port
        assert 1024 <= chosen <= 65535


# ---------------------------------------------------------------------------
# Docker mode tests — all docker CLI calls are mocked; no real Docker needed.
# ---------------------------------------------------------------------------


def test_daemon_status_reports_docker_when_container_file_present(tmp_path, monkeypatch):
    """When daemon.container exists and container is alive, status reports docker."""
    paths = daemon_paths(tmp_path, ensure=True)
    paths.container_file.write_text("abc123def456")
    monkeypatch.setattr("jig.container.container_alive", lambda cid: True)
    status = daemon_status(tmp_path)
    assert status.running is True
    assert status.kind == "docker"
    assert status.container_id == "abc123def456"


def test_daemon_status_marks_docker_stale_when_container_dead(tmp_path, monkeypatch):
    paths = daemon_paths(tmp_path, ensure=True)
    paths.container_file.write_text("dead-container-id")
    monkeypatch.setattr("jig.container.container_alive", lambda cid: False)
    status = daemon_status(tmp_path)
    assert status.running is False
    assert status.stale is True
    assert status.container_id == "dead-container-id"


def test_daemon_start_docker_writes_container_file(tmp_path, monkeypatch):
    """When docker=True, daemon_start calls run_detached_container and
    records the container id."""
    fake_id = "fake-container-12345"
    monkeypatch.setattr("jig.container.docker_available", lambda: True)
    monkeypatch.setattr("jig.container.image_exists", lambda: True)
    monkeypatch.setattr(
        "jig.container.run_detached_container",
        lambda *a, **kw: fake_id,
    )
    monkeypatch.setattr("jig.container.container_alive", lambda cid: True)

    result = daemon_start(tmp_path, docker=True)
    assert result.container_id == fake_id
    assert (tmp_path / ".jig" / "run" / "daemon.container").read_text() == fake_id


def test_daemon_start_docker_removes_orphan_containers_before_launch(
    tmp_path, monkeypatch
):
    """When containers labeled with this project exist but daemon_status
    says not running, they're orphans — remove them before launching and
    report them in DaemonStartResult.orphans_removed."""
    monkeypatch.setattr("jig.container.docker_available", lambda: True)
    monkeypatch.setattr("jig.container.image_exists", lambda: True)
    monkeypatch.setattr(
        "jig.container.run_detached_container",
        lambda *a, **kw: "new-container-id",
    )
    monkeypatch.setattr("jig.container.container_alive", lambda cid: True)

    orphan_ids = ["orphan-cid-1111111111", "orphan-cid-2222222222"]
    monkeypatch.setattr(
        "jig.container.find_project_containers",
        lambda path: list(orphan_ids),
    )
    removed: list[str] = []

    def fake_remove(cid: str) -> bool:
        removed.append(cid)
        return True

    monkeypatch.setattr("jig.container.force_remove_container", fake_remove)

    result = daemon_start(tmp_path, docker=True)
    assert removed == orphan_ids
    assert result.orphans_removed == tuple(c[:12] for c in orphan_ids)
    assert result.container_id == "new-container-id"


def test_daemon_start_docker_raises_when_image_missing(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr("jig.container.docker_available", lambda: True)
    monkeypatch.setattr("jig.container.image_exists", lambda: False)
    with pytest.raises(RuntimeError, match=r"jig Docker image not built"):
        daemon_start(tmp_path, docker=True)


def test_daemon_start_docker_raises_when_docker_unavailable(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr("jig.container.docker_available", lambda: False)
    with pytest.raises(RuntimeError, match=r"docker not available"):
        daemon_start(tmp_path, docker=True)


def test_daemon_stop_docker_calls_docker_stop(tmp_path, monkeypatch):
    """daemon_stop in docker mode calls docker stop on the container id."""
    paths = daemon_paths(tmp_path, ensure=True)
    paths.container_file.write_text("test-container-id")
    paths.socket_addr_file.write_text("ws://127.0.0.1:19100")
    monkeypatch.setattr("jig.container.container_alive", lambda cid: True)

    stopped_ids: list[str] = []

    def fake_stop(cid: str, *, timeout: int = 5) -> bool:
        stopped_ids.append(cid)
        return True

    monkeypatch.setattr("jig.container.stop_detached_container", fake_stop)

    assert daemon_stop(tmp_path) is True
    assert "test-container-id" in stopped_ids
    assert not paths.container_file.exists()
