---
title: Textual TUI — Implementation Plan
type: plan
status: draft
owner: brent
created: 2026-04-28
updated: 2026-04-28
design: ./design.md
---

# Textual TUI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Bun + Gridland JSX TUI (`tui/`) with a Python Textual app (`jig/tui/`) that is the only operator-facing entry point. `jig` (no args) launches the TUI, which connects to a background `jig daemon` that runs the orchestrator. Free-text input flows through a "concierge" LLM agent for help and intent dispatch.

**Architecture:** Two long-lived processes — daemon (orchestrator + agents + WebSocket) and TUI client (Textual). Four screens (Now, Tickets, Spec, Events) with tab navigation. Hybrid input: slash commands fast-path, concierge agent for free-text. CI / scripting via `jig --print "/<command>"`.

**Tech Stack:** Python 3.12+, Textual ≥0.80, existing `websockets` for transport, existing `claude-agent-sdk` for the concierge spawn, existing Pydantic models everywhere.

---

## Overview

Five phases. Phase 1 lays groundwork (daemon + protocol). Phase 2 ships the TUI shell with Now in idle mode. Phase 3 makes Now real (init flow inline + concierge agent). Phase 4 fleshes out Tickets / Spec / Events. Phase 5 (same PR as Phase 4 completion) deletes the Bun TUI and lands the dependency change.

Each phase ends in a green test suite and at least one commit per task.

## Preconditions

- [ ] Design doc approved (`./design.md`).
- [ ] Latest `develop` branch.
- [ ] `uv sync` runs cleanly.
- [ ] Test suite green (`uv run pytest tests/ -q`).
- [ ] `textual` not yet a dep (Phase 5 adds it). Phase 2 onward adds it as a *dev dep* to keep the Bun TUI install path uncluttered until cutover; Phase 5 promotes it to a runtime dep.

---

## Phase 1: Daemon refactor

**What:** Split `jig start`'s "orchestrator + WebSocket server" from "stays in foreground." Add `jig daemon start | stop | status` subcommands that fork to background, write PID + Unix-socket address to `.jig/run/`, and provide lifecycle introspection. Extend `ws_server.py` with typed snapshot messages on subscribe and typed event messages.

**Why:** Background work (agents running for minutes-to-hours) requires the orchestrator to outlive any single TUI session. The TUI is just a client.

**Verify:** `uv run pytest tests/test_daemon.py tests/test_ws_server_protocol.py -v` passes; existing Bun TUI still works against the upgraded protocol (manual smoke).

### 1.1 Daemon process management

**Files:**
- Create: `jig/daemon.py`
- Create: `tests/test_daemon.py`
- Modify: `jig/cli.py` (add `daemon` group)

- [ ] **Step 1: Write the failing test for the daemon paths helper**

```python
# tests/test_daemon.py
from pathlib import Path

from jig.daemon import daemon_paths


def test_daemon_paths_returns_pid_and_socket_under_jig_run(tmp_path):
    paths = daemon_paths(tmp_path)
    assert paths.pid_file == tmp_path / ".jig" / "run" / "daemon.pid"
    assert paths.socket_addr_file == tmp_path / ".jig" / "run" / "daemon.addr"
    assert paths.run_dir == tmp_path / ".jig" / "run"


def test_daemon_paths_creates_run_dir_when_requested(tmp_path):
    paths = daemon_paths(tmp_path, ensure=True)
    assert paths.run_dir.is_dir()
```

- [ ] **Step 2: Run test — expect FAIL (no module)**

`uv run pytest tests/test_daemon.py::test_daemon_paths_returns_pid_and_socket_under_jig_run -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Create the module with the path helper**

```python
# jig/daemon.py
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

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DaemonPaths:
    run_dir: Path
    pid_file: Path
    socket_addr_file: Path


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
    )
```

- [ ] **Step 4: Run tests — expect PASS**

`uv run pytest tests/test_daemon.py -v`
Expected: 2 PASS.

- [ ] **Step 5: Add status check helpers**

```python
# tests/test_daemon.py
import os
import signal

from jig.daemon import daemon_status, DaemonStatus


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
```

- [ ] **Step 6: Implement `daemon_status`**

```python
# jig/daemon.py — append
import os


@dataclass(frozen=True)
class DaemonStatus:
    running: bool
    pid: int | None = None
    stale: bool = False  # True when PID file exists but no live process


def daemon_status(project_path: Path) -> DaemonStatus:
    """Inspect the daemon's run-state via its PID file.

    Returns ``running=True`` only if the PID file exists AND the
    process is alive. ``stale=True`` indicates a leftover PID file
    pointing at a dead process — caller should clean it up before
    starting a new daemon.
    """
    paths = daemon_paths(project_path)
    if not paths.pid_file.is_file():
        return DaemonStatus(running=False)
    try:
        pid = int(paths.pid_file.read_text().strip())
    except ValueError:
        return DaemonStatus(running=False, stale=True)
    if _process_alive(pid):
        return DaemonStatus(running=True, pid=pid)
    return DaemonStatus(running=False, stale=True)


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
```

- [ ] **Step 7: Run tests — expect PASS**

`uv run pytest tests/test_daemon.py -v`
Expected: 5 PASS.

- [ ] **Step 8: Add daemon start (background fork)**

```python
# tests/test_daemon.py
import time

from jig.daemon import daemon_start, daemon_stop


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
```

- [ ] **Step 9: Implement `daemon_start` and `daemon_stop`**

```python
# jig/daemon.py — append
import subprocess
import time
from typing import Sequence


@dataclass(frozen=True)
class DaemonStartResult:
    pid: int
    addr: str  # e.g. "ws://127.0.0.1:9100"


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
    # Detach: redirect stdio, new session, no inherit-from-shell-quitting.
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    paths.pid_file.write_text(str(proc.pid))
    addr = f"ws://127.0.0.1:{ws_port}"
    paths.socket_addr_file.write_text(addr)
    return DaemonStartResult(pid=proc.pid, addr=addr)


class DaemonAlreadyRunning(RuntimeError):
    """Raised when daemon_start finds an existing live daemon."""


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
        os.kill(status.pid, signal.SIGKILL)
    daemon_paths(project_path).pid_file.unlink(missing_ok=True)
    daemon_paths(project_path).socket_addr_file.unlink(missing_ok=True)
    return True
```

- [ ] **Step 10: Run tests — expect PASS**

`uv run pytest tests/test_daemon.py -v`
Expected: 6 PASS. Note: the start/stop test calls `subprocess.Popen` for `sleep 60`, which is fine in CI.

- [ ] **Step 11: Add the `jig daemon` CLI group**

```python
# jig/cli.py — add at bottom of file (after existing commands)
@cli.group(name="daemon")
def daemon_group() -> None:
    """Manage the background daemon (orchestrator + WebSocket server)."""


@daemon_group.command(name="start")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--ws-port", default=9100, type=int, show_default=True)
def daemon_start_cmd(path: Path, ws_port: int) -> None:
    """Start the daemon in the background."""
    from jig.daemon import daemon_start, DaemonAlreadyRunning

    try:
        result = daemon_start(path, ws_port=ws_port)
    except DaemonAlreadyRunning as exc:
        raise click.ClickException(str(exc))
    click.echo(f"daemon started: pid={result.pid} addr={result.addr}")


@daemon_group.command(name="stop")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def daemon_stop_cmd(path: Path) -> None:
    """Stop the daemon if running."""
    from jig.daemon import daemon_stop

    if daemon_stop(path):
        click.echo("daemon stopped")
    else:
        click.echo("no daemon was running")


@daemon_group.command(name="status")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def daemon_status_cmd(path: Path) -> None:
    """Print daemon status (running? pid? addr?)."""
    from jig.daemon import daemon_status, daemon_paths

    status = daemon_status(path)
    if not status.running:
        if status.stale:
            click.echo("daemon: not running (stale PID file present)")
        else:
            click.echo("daemon: not running")
        return
    addr_file = daemon_paths(path).socket_addr_file
    addr = addr_file.read_text().strip() if addr_file.is_file() else "?"
    click.echo(f"daemon: running pid={status.pid} addr={addr}")


@daemon_group.command(name="serve", hidden=True)
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--ws-port", default=9100, type=int)
def daemon_serve_cmd(path: Path, ws_port: int) -> None:
    """Internal: actually host the orchestrator. Called by daemon_start
    via the forked subprocess; not for direct user invocation."""
    # This is the existing `jig start` body, refactored to run forever
    # in this process. Will be filled in as Phase 1 progresses.
    from jig.cli import _run_orchestrator_loop  # added below

    _run_orchestrator_loop(path, ws_port)
```

- [ ] **Step 12: Extract `_run_orchestrator_loop` from `jig start`**

The existing `jig start` command body (in `jig/cli.py`, around line 88) does:
1. Container check / Docker re-exec
2. `configure_logging`
3. Spawn orchestrator + WebSocket server
4. Run forever until SIGINT

For Phase 1, extract steps 2-4 into `_run_orchestrator_loop(path, ws_port)`. Keep step 1 (Docker) only in the foreground `jig start` path — `daemon serve` runs natively (no Docker re-exec). `jig start` is left as-is for now (it'll be deleted in Phase 5).

```python
# jig/cli.py — extract from existing `start` command body
def _run_orchestrator_loop(path: Path, ws_port: int) -> None:
    """Run the orchestrator + WebSocket server in this process forever.
    Used by both `jig start` (foreground) and `jig daemon serve` (background)."""
    from jig.catalog import CatalogError, validate_catalog
    from jig.events import EventEmitter
    from jig.logging_setup import configure_logging
    from jig.orchestrator import Orchestrator
    from jig.ws_server import WebSocketServer

    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(
            f"Jig not initialized in {path}. Run `jig init` first."
        )
    try:
        validate_catalog(path)
    except CatalogError as exc:
        raise click.ClickException(f"Catalog validation failed: {exc}")

    log_file = configure_logging(path, verbose=False)
    click.echo(f"Logging to {log_file}")

    async def run_daemon() -> None:
        emitter = EventEmitter()
        # ... existing orchestrator + ws_server setup ...
        # Refactor the body of the current `start` command's `run_daemon`
        # function into here. The implementer should copy the existing
        # asyncio body, ensuring it shuts down cleanly on SIGTERM.

    asyncio.run(run_daemon())
```

> **Note for the implementer:** the existing `start` command body has the full orchestrator + WebSocket setup. Open `jig/cli.py` around line 88-160 and copy the `async def run_daemon(): ...` body into `_run_orchestrator_loop`. Keep `jig start` working by having it also call `_run_orchestrator_loop` after the Docker check (it currently inlines the same code).

- [ ] **Step 13: Run tests + lint**

`uv run ruff check jig/ tests/ && uv run pytest tests/ -q`
Expected: full suite green; daemon tests pass.

- [ ] **Step 14: Commit**

```bash
git add jig/daemon.py jig/cli.py tests/test_daemon.py
git commit -m "feat(daemon): jig daemon start/stop/status with PID + socket addr in .jig/run/"
```

### 1.2 Snapshot + typed event protocol on ws_server

**Files:**
- Modify: `jig/ws_server.py`
- Create: `tests/test_ws_server_protocol.py`

The existing `ws_server` broadcasts raw bus messages to clients. This task adds two new outbound message kinds: `snapshot` (sent once per topic on subscribe) and `event` (typed kind + data per state change).

- [ ] **Step 1: Read the existing protocol shape**

Run: `grep -n "json.dumps\|self._safe_send" jig/ws_server.py | head -20`
Note current message shapes — they're ad-hoc (e.g., `{"ok": true, "tickets": [...]}`). The new protocol is structured:

```json
{"type": "snapshot", "topic": "tickets", "data": [...]}
{"type": "event", "topic": "tickets", "kind": "updated", "data": {...}}
```

- [ ] **Step 2: Write a failing test for the snapshot envelope helper**

```python
# tests/test_ws_server_protocol.py
import pytest

from jig.ws_server import snapshot_envelope, event_envelope


def test_snapshot_envelope_shape():
    msg = snapshot_envelope("tickets", [{"id": "brief"}])
    assert msg == {
        "type": "snapshot",
        "topic": "tickets",
        "data": [{"id": "brief"}],
    }


def test_event_envelope_shape():
    msg = event_envelope("tickets", "updated", {"id": "brief", "status": "needs_info"})
    assert msg == {
        "type": "event",
        "topic": "tickets",
        "kind": "updated",
        "data": {"id": "brief", "status": "needs_info"},
    }
```

- [ ] **Step 3: Run — expect FAIL**

`uv run pytest tests/test_ws_server_protocol.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Add the envelope helpers**

```python
# jig/ws_server.py — add at module level (above WebSocketServer)
from typing import Any


_VALID_TOPICS = {"tickets", "threads", "agents", "spec", "events"}


def snapshot_envelope(topic: str, data: Any) -> dict:
    """Build a snapshot message for a topic (sent once per subscribe)."""
    if topic not in _VALID_TOPICS:
        raise ValueError(f"unknown topic {topic!r}; expected one of {_VALID_TOPICS}")
    return {"type": "snapshot", "topic": topic, "data": data}


def event_envelope(topic: str, kind: str, data: Any) -> dict:
    """Build a typed event message (sent per state change)."""
    if topic not in _VALID_TOPICS:
        raise ValueError(f"unknown topic {topic!r}; expected one of {_VALID_TOPICS}")
    return {"type": "event", "topic": topic, "kind": kind, "data": data}
```

- [ ] **Step 5: Run — expect PASS**

`uv run pytest tests/test_ws_server_protocol.py -v`
Expected: 2 PASS.

- [ ] **Step 6: Wire snapshot delivery on subscribe**

The TUI client will send `{"type": "subscribe", "topics": ["tickets", "threads"]}` on connect. The server responds with one `snapshot` per topic, then streams events. Add a subscribe handler.

```python
# jig/ws_server.py — extend _handle_incoming
async def _handle_incoming(self, websocket, raw: str) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as exc:
        await self._safe_send(
            websocket, json.dumps({"ok": False, "error": str(exc)})
        )
        return
    msg_type = msg.get("type")
    if msg_type == "subscribe":
        topics = msg.get("topics", [])
        for topic in topics:
            snapshot = await self._build_snapshot(topic)
            await self._safe_send(
                websocket, json.dumps(snapshot_envelope(topic, snapshot))
            )
        return
    if msg_type == "command":
        # New protocol — typed command. Existing code uses bare keys
        # like `{"command": "create_ticket", ...}`; both supported.
        await self._dispatch_command(websocket, msg.get("name"), msg.get("args", {}))
        return
    # Fallback: legacy command shape (existing Bun TUI uses this).
    command = msg.get("command")
    if command:
        await self._dispatch_command(websocket, command, msg)
        return
    await self._safe_send(
        websocket, json.dumps({"ok": False, "error": f"unknown message {msg!r}"})
    )


async def _build_snapshot(self, topic: str) -> Any:
    """Per-topic initial snapshot."""
    if topic == "tickets":
        if self._orch is None:
            return []
        all_tickets = await self._orch.tickets.list_all()
        return [t.model_dump(mode="json") for t in all_tickets]
    if topic == "spec":
        # Read project.structured.yaml directly (orchestrator owns the path)
        from jig.spec_schema import StructuredSpec
        import yaml
        spec_file = self._project_path / ".jig" / "spec" / "project.structured.yaml"
        if not spec_file.is_file():
            return None
        data = yaml.safe_load(spec_file.read_text()) or {}
        spec = StructuredSpec.model_validate(data)
        return spec.model_dump(mode="json", by_alias=True)
    if topic == "agents":
        if self._orch is None:
            return []
        return await self._orch.list_active_agents()  # method to be added if missing
    if topic == "events":
        # Last 100 messages from the bus's in-memory ring buffer
        if self._orch is None:
            return []
        return [m.model_dump(mode="json") for m in self._orch.bus.recent(limit=100)]
    if topic == "threads":
        # Snapshot is per-ticket; subscribers fetch on demand via command.
        # Return empty by default.
        return []
    raise ValueError(f"unknown topic {topic!r}")
```

> **Note for the implementer:** some methods called above (`tickets.list_all`, `orch.list_active_agents`, `bus.recent`) may need to be added if they don't exist. Check the existing API and add minimal wrappers if needed; keep the wrappers in their owning module, not here. The `self._project_path` attribute may need adding to `WebSocketServer.__init__` — pass it through from `jig.cli`'s call site.

- [ ] **Step 7: Add a test for snapshot delivery**

```python
# tests/test_ws_server_protocol.py
import asyncio
import json

import pytest
import websockets

from jig.events import EventEmitter
from jig.orchestrator import Orchestrator
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType
from jig.ws_server import WebSocketServer


@pytest.mark.asyncio
async def test_subscribe_to_tickets_yields_snapshot(tmp_path):
    # Set up minimal orchestrator
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    await tickets.create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )

    orch = Orchestrator(
        tickets=tickets, threads=threads, memory=memory, bus=bus,
        emitter=EventEmitter(), project_path=tmp_path,
    )
    server = WebSocketServer(
        orch.emitter, port=0, orchestrator=orch, project_path=tmp_path,
    )
    await server.start()
    try:
        url = f"ws://127.0.0.1:{server.port}"
        async with websockets.connect(url) as client:
            await client.send(json.dumps({"type": "subscribe", "topics": ["tickets"]}))
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            msg = json.loads(raw)
            assert msg["type"] == "snapshot"
            assert msg["topic"] == "tickets"
            assert any(t["id"] == "brief" for t in msg["data"])
    finally:
        await server.stop()
```

- [ ] **Step 8: Run — expect PASS**

`uv run pytest tests/test_ws_server_protocol.py::test_subscribe_to_tickets_yields_snapshot -v`
Expected: PASS.

- [ ] **Step 9: Wire typed event publishing on bus messages**

The existing `_relay_events` broadcasts every bus message verbatim. Add a typed wrapper that maps bus payloads to `{topic, kind, data}`:

```python
# jig/ws_server.py — modify _relay_events
async def _relay_events(self) -> None:
    while True:
        event = await self._queue.get()
        # Existing raw-message broadcast (Bun TUI still listens for it):
        message = event.to_json()
        self._history.append(message)
        for client in list(self._clients):
            try:
                await client.send(message)
            except websockets.ConnectionClosed:
                self._clients.discard(client)
        # NEW: also publish as typed event for new TUI clients
        typed = self._classify_event(event)
        if typed is not None:
            typed_msg = json.dumps(typed)
            for client in list(self._clients):
                try:
                    await client.send(typed_msg)
                except websockets.ConnectionClosed:
                    self._clients.discard(client)


def _classify_event(self, event) -> dict | None:
    """Map a bus event to a typed {topic, kind, data} envelope, or None
    to skip (event has no relevant typed projection)."""
    payload = event.data or {}
    kind = payload.get("kind")
    if kind in ("ticket_updated", "ticket_created"):
        return event_envelope("tickets", kind.removeprefix("ticket_"), payload)
    if kind in ("comment_posted",):
        return event_envelope("threads", "posted", payload)
    if event.type in ("agent_text", "agent_tool", "agent_tool_result", "agent_run"):
        return event_envelope("agents", event.type.removeprefix("agent_"), payload)
    # Default: surface on `events` topic for the events screen
    return event_envelope("events", event.type, payload)
```

- [ ] **Step 10: Test typed event delivery**

```python
# tests/test_ws_server_protocol.py
@pytest.mark.asyncio
async def test_ticket_update_yields_typed_event(tmp_path):
    """When a ticket is updated, subscribers see the typed event
    {topic: tickets, kind: updated, data: {...}}."""
    # Same setup as above; abbreviated here.
    # ... (set up server, subscribe, then update a ticket via orch)
    # Assert: client receives a message with type=event, topic=tickets, kind=updated
    pass  # implementer fills in following the prior test's pattern
```

- [ ] **Step 11: Run all ws_server tests**

`uv run pytest tests/test_ws_server_protocol.py -v && uv run pytest tests/ -q`
Expected: all PASS, no regressions in existing ws_server tests.

- [ ] **Step 12: Commit**

```bash
git add jig/ws_server.py tests/test_ws_server_protocol.py
git commit -m "feat(ws-server): typed snapshot + event protocol; backward-compatible with Bun TUI"
```

### 1.3 Textual + asyncio integration spike

**Files:**
- Create: `scripts/spike_textual_asyncio.py` (throwaway, deleted at end of Phase 1)

**Why:** Textual runs its own event loop. The TUI will need to consume WebSocket events (asyncio) AND drive Textual reactive updates. Verify this works cleanly before committing to the design.

- [ ] **Step 1: Add textual as a dev dependency**

```bash
# pyproject.toml — add to [dependency-groups].dev
"textual>=0.80",
```

Then `uv sync`.

- [ ] **Step 2: Write the spike**

```python
# scripts/spike_textual_asyncio.py
"""Spike: verify Textual + asyncio + websockets integration.

Goal: a Textual app that opens a WebSocket connection and prints
incoming messages to a RichLog widget without blocking the UI.

Throwaway after Phase 1. Run via:
  uv run python scripts/spike_textual_asyncio.py
"""
import asyncio
import json

import websockets
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog


class SpikeApp(App):
    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield RichLog(id="log", auto_scroll=True)
        yield Footer()

    async def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        log.write("connecting…")
        # Spawn an async task; Textual's worker system handles it.
        self.run_worker(self._consume_ws(), exclusive=True)

    async def _consume_ws(self) -> None:
        log = self.query_one("#log", RichLog)
        try:
            async with websockets.connect("ws://127.0.0.1:9100") as ws:
                await ws.send(json.dumps(
                    {"type": "subscribe", "topics": ["tickets"]}
                ))
                async for raw in ws:
                    log.write(raw[:200])
        except OSError as exc:
            log.write(f"connection failed: {exc}")


if __name__ == "__main__":
    SpikeApp().run()
```

- [ ] **Step 3: Run the spike against a live daemon**

```bash
# In one terminal
jig daemon start
# In another
uv run python scripts/spike_textual_asyncio.py
```

Expected: Textual app launches, shows "connecting…", then displays the snapshot message from the WebSocket. `q` quits cleanly.

- [ ] **Step 4: If it works, commit and clean up**

```bash
rm scripts/spike_textual_asyncio.py
git add pyproject.toml uv.lock
git commit -m "chore(deps): add textual as dev dep for upcoming TUI work"
```

If the spike fails (e.g., Textual workers don't compose with `websockets` cleanly), STOP and report — the architecture needs adjustment before Phase 2.

---

## Phase 2: TUI shell + Now (idle / observation modes)

**What:** Create `jig/tui/` module with the Textual app shell, four screens (only Now functional), top tab bar, persistent footer with daemon status, slash command parser with `/help`, `/status`, `/quit`. Connection lifecycle (connecting / connected / reconnecting / disconnected).

**Why:** Foundation for everything Phase 3+ adds. Operator can launch the TUI, see screens, switch tabs, run trivial slash commands.

**Verify:** `uv run pytest tests/test_tui_shell.py -v` passes; `uv run jig` (no args, with daemon running) launches the TUI and renders the Now screen.

### 2.1 TUI module skeleton

**Files:**
- Create: `jig/tui/__init__.py`
- Create: `jig/tui/app.py`
- Create: `jig/tui/screens/__init__.py`
- Create: `jig/tui/screens/now.py`
- Create: `jig/tui/screens/tickets.py`
- Create: `jig/tui/screens/spec.py`
- Create: `jig/tui/screens/events.py`
- Create: `tests/test_tui_shell.py`

- [ ] **Step 1: Create the four placeholder screens**

```python
# jig/tui/screens/now.py
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class NowScreen(Screen):
    """The active-interaction screen — scrolling transcript with
    structured prompts inline. Idle implementation in v1; full
    implementation in Phase 3."""

    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Static("Now — idle\n\n(Phase 3 will add the transcript and input)")
```

```python
# jig/tui/screens/tickets.py
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class TicketsScreen(Screen):
    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Static("Tickets — coming in Phase 4")
```

```python
# jig/tui/screens/spec.py
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class SpecScreen(Screen):
    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Static("Spec — coming in Phase 4")
```

```python
# jig/tui/screens/events.py
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static


class EventsScreen(Screen):
    BINDINGS = []

    def compose(self) -> ComposeResult:
        yield Static("Events — coming in Phase 4")
```

- [ ] **Step 2: Create the App with tab navigation**

```python
# jig/tui/app.py
"""Top-level Textual app for jig."""
from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Static, TabbedContent, TabPane

from jig.tui.screens.events import EventsScreen
from jig.tui.screens.now import NowScreen
from jig.tui.screens.spec import SpecScreen
from jig.tui.screens.tickets import TicketsScreen


class JigApp(App):
    """The jig TUI. Single app, four tabbed screens."""

    CSS_PATH = "app.tcss"
    TITLE = "jig"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("1", "switch_screen('now')", "Now", show=False),
        Binding("2", "switch_screen('tickets')", "Tickets", show=False),
        Binding("3", "switch_screen('spec')", "Spec", show=False),
        Binding("4", "switch_screen('events')", "Events", show=False),
    ]

    def __init__(self, project_path: Path) -> None:
        super().__init__()
        self.project_path = project_path

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="now-pane"):
            with TabPane("Now", id="now-pane"):
                yield NowScreen()
            with TabPane("Tickets", id="tickets-pane"):
                yield TicketsScreen()
            with TabPane("Spec", id="spec-pane"):
                yield SpecScreen()
            with TabPane("Events", id="events-pane"):
                yield EventsScreen()
        yield Footer()

    def action_switch_screen(self, screen_id: str) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = f"{screen_id}-pane"

    def action_help(self) -> None:
        # Placeholder — full help overlay in Phase 2.4
        self.notify("Help overlay coming in Phase 2.4")
```

- [ ] **Step 3: Add minimal CSS**

```css
/* jig/tui/app.tcss */
Screen {
    background: $surface;
}

TabbedContent {
    height: 1fr;
}
```

- [ ] **Step 4: Wire `jig` (no args) to launch the TUI**

```python
# jig/cli.py — add (or modify the existing cli group)
@cli.command(name="tui", hidden=True)
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def tui_cmd(path: Path) -> None:
    """Launch the Textual TUI (default action when `jig` is invoked
    with no args via __main__.py)."""
    from jig.tui.app import JigApp

    JigApp(project_path=path).run()


# Modify jig/__main__.py to dispatch to tui by default:
```

```python
# jig/__main__.py
"""Entry point for ``python -m jig``.

If invoked with no args (or `jig` shell wrapper with no args),
launches the TUI. Otherwise dispatches to the click CLI.
"""
import sys

from jig.cli import cli


def main() -> None:
    if len(sys.argv) == 1:
        # Auto-route to the TUI
        sys.argv = ["jig", "tui"]
    cli()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Test that the app instantiates**

```python
# tests/test_tui_shell.py
"""Smoke tests for the TUI shell.

These don't run the TUI in a real terminal (Textual provides a
``Pilot`` for headless testing). They verify the app composes
correctly, screens are mounted, and tab navigation works.
"""
import pytest
from pathlib import Path

from jig.tui.app import JigApp


@pytest.mark.asyncio
async def test_app_composes_with_four_screens(tmp_path: Path):
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        # All four panes should exist
        for screen_id in ("now-pane", "tickets-pane", "spec-pane", "events-pane"):
            assert app.query_one(f"#{screen_id}") is not None


@pytest.mark.asyncio
async def test_app_switches_screens_via_hotkey(tmp_path: Path):
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        tabs = app.query_one("TabbedContent")
        assert tabs.active == "tickets-pane"
        await pilot.press("1")
        assert tabs.active == "now-pane"
```

- [ ] **Step 6: Run tests**

`uv run pytest tests/test_tui_shell.py -v`
Expected: 2 PASS.

- [ ] **Step 7: Manual smoke test**

```bash
jig daemon start
jig  # opens the TUI
# Press 1, 2, 3, 4 to switch tabs
# Press q to quit
jig daemon stop
```

- [ ] **Step 8: Commit**

```bash
git add jig/tui/ jig/cli.py jig/__main__.py tests/test_tui_shell.py
git commit -m "feat(tui): Textual app shell with four placeholder screens"
```

### 2.2 Daemon connection + footer status

**Files:**
- Create: `jig/tui/daemon_client.py`
- Modify: `jig/tui/app.py`
- Create: `tests/test_tui_daemon_client.py`

- [ ] **Step 1: Write the daemon client**

```python
# jig/tui/daemon_client.py
"""WebSocket client to the jig daemon.

Wraps the websockets library in a simple async API the TUI uses to
subscribe to topics, send commands, and receive snapshots/events.
Auto-reconnects on disconnect with exponential backoff.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

import websockets
from websockets.asyncio.client import ClientConnection


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class DaemonClient:
    url: str
    state: ConnectionState = ConnectionState.DISCONNECTED
    _ws: ClientConnection | None = None
    _reconnect_delay: float = 1.0
    _max_delay: float = 30.0

    async def connect(self) -> None:
        self.state = ConnectionState.CONNECTING
        self._ws = await websockets.connect(self.url, ping_interval=20)
        self.state = ConnectionState.CONNECTED
        self._reconnect_delay = 1.0

    async def close(self) -> None:
        self.state = ConnectionState.DISCONNECTED
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def subscribe(self, topics: list[str]) -> None:
        if self._ws is None:
            raise RuntimeError("not connected")
        await self._ws.send(json.dumps({"type": "subscribe", "topics": topics}))

    async def send_command(self, name: str, args: dict) -> None:
        if self._ws is None:
            raise RuntimeError("not connected")
        await self._ws.send(json.dumps({"type": "command", "name": name, "args": args}))

    async def messages(self) -> AsyncIterator[dict]:
        """Yield typed messages until the connection closes."""
        if self._ws is None:
            raise RuntimeError("not connected")
        async for raw in self._ws:
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue  # ignore malformed frames

    async def run_with_reconnect(
        self,
        on_message,  # async callable: (msg: dict) -> None
        on_state_change,  # callable: (state: ConnectionState) -> None
        topics: list[str],
    ) -> None:
        """Connect-loop with exponential backoff. Reconnects on
        disconnect; never raises (logs and retries)."""
        while True:
            try:
                await self.connect()
                on_state_change(self.state)
                await self.subscribe(topics)
                async for msg in self.messages():
                    await on_message(msg)
            except (OSError, websockets.exceptions.WebSocketException):
                self.state = ConnectionState.RECONNECTING
                on_state_change(self.state)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_delay)
            else:
                # Clean disconnect — also reconnect.
                self.state = ConnectionState.RECONNECTING
                on_state_change(self.state)
                await asyncio.sleep(1)
```

- [ ] **Step 2: Wire the client into the App; show state in footer**

```python
# jig/tui/app.py — modify
from textual.reactive import reactive

from jig.tui.daemon_client import ConnectionState, DaemonClient


class JigApp(App):
    # ...existing...

    daemon_state: reactive[ConnectionState] = reactive(ConnectionState.DISCONNECTED)

    def __init__(self, project_path: Path) -> None:
        super().__init__()
        self.project_path = project_path
        # Discover daemon socket address
        from jig.daemon import daemon_paths
        addr_file = daemon_paths(project_path).socket_addr_file
        addr = addr_file.read_text().strip() if addr_file.is_file() else "ws://127.0.0.1:9100"
        self.client = DaemonClient(url=addr)

    async def on_mount(self) -> None:
        # Start the connection loop
        self.run_worker(
            self.client.run_with_reconnect(
                on_message=self._dispatch_message,
                on_state_change=self._on_daemon_state,
                topics=["tickets", "spec", "agents", "events"],
            ),
            exclusive=True,
            name="daemon-client",
        )

    async def _dispatch_message(self, msg: dict) -> None:
        # Phase 2: just stash for now; screens read from app state
        pass

    def _on_daemon_state(self, state: ConnectionState) -> None:
        self.daemon_state = state

    def watch_daemon_state(self, new: ConnectionState) -> None:
        # Reactive: footer text updates when state changes
        # Phase 2: notify; full footer rendering in Phase 2.3
        self.notify(f"daemon: {new.value}")
```

- [ ] **Step 3: Add a custom footer widget that shows daemon state**

```python
# jig/tui/widgets/__init__.py
# (empty file)
```

```python
# jig/tui/widgets/footer.py
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

from jig.tui.daemon_client import ConnectionState


_COLORS = {
    ConnectionState.CONNECTED: "green",
    ConnectionState.CONNECTING: "yellow",
    ConnectionState.RECONNECTING: "yellow",
    ConnectionState.DISCONNECTED: "red",
}


class JigFooter(Widget):
    """Persistent footer: daemon state + key hints."""

    DEFAULT_CSS = """
    JigFooter {
        height: 1;
        dock: bottom;
        background: $panel;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._daemon_text = Static("daemon: ?")

    def compose(self) -> ComposeResult:
        yield self._daemon_text

    def update_daemon_state(self, state: ConnectionState) -> None:
        color = _COLORS.get(state, "white")
        self._daemon_text.update(f"[{color}]daemon: {state.value}[/{color}]")
```

Wire `JigFooter.update_daemon_state` into the App's `watch_daemon_state` reactive:

```python
# jig/tui/app.py — modify watch_daemon_state
def watch_daemon_state(self, new: ConnectionState) -> None:
    footer = self.query_one(JigFooter)
    footer.update_daemon_state(new)
```

- [ ] **Step 4: Test the daemon client (no real WebSocket — mock)**

```python
# tests/test_tui_daemon_client.py
import pytest

from jig.tui.daemon_client import ConnectionState, DaemonClient


def test_daemon_client_starts_disconnected():
    c = DaemonClient(url="ws://localhost:9999")
    assert c.state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_send_command_raises_when_not_connected():
    c = DaemonClient(url="ws://localhost:9999")
    with pytest.raises(RuntimeError, match="not connected"):
        await c.send_command("init", {"name": "x"})
```

- [ ] **Step 5: Manual integration test**

```bash
jig daemon start
jig  # TUI launches; footer should show "daemon: connected" within ~1s
jig daemon stop  # in another terminal — footer flips to "daemon: reconnecting"
jig daemon start  # footer flips back to "daemon: connected"
```

- [ ] **Step 6: Commit**

```bash
git add jig/tui/daemon_client.py jig/tui/widgets/ jig/tui/app.py tests/test_tui_daemon_client.py
git commit -m "feat(tui): daemon client + footer with connection state"
```

### 2.3 Now screen idle mode: scrollback + input + slash parser

**Files:**
- Modify: `jig/tui/screens/now.py`
- Create: `jig/tui/slash.py`
- Create: `tests/test_tui_slash.py`

- [ ] **Step 1: Define the slash command parser**

```python
# jig/tui/slash.py
"""Parse and dispatch slash commands.

Slash commands are the fast path for known operations. Free-text
inputs (anything not starting with `/`) go to the concierge agent in
Phase 3.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass
class ParsedSlash:
    name: str
    args: list[str]


class SlashParseError(ValueError):
    """Raised when a slash command is malformed."""


def parse_slash(line: str) -> ParsedSlash:
    """Parse `/name arg1 arg2 ...` into a ParsedSlash.

    Quoting follows shell rules (shlex). Raises SlashParseError if the
    line doesn't start with `/` or is empty after the slash.
    """
    line = line.strip()
    if not line.startswith("/"):
        raise SlashParseError(f"not a slash command: {line!r}")
    body = line[1:].strip()
    if not body:
        raise SlashParseError("empty slash command")
    try:
        parts = shlex.split(body)
    except ValueError as exc:
        raise SlashParseError(f"malformed quoting: {exc}") from exc
    return ParsedSlash(name=parts[0], args=parts[1:])
```

- [ ] **Step 2: Test the parser**

```python
# tests/test_tui_slash.py
import pytest

from jig.tui.slash import ParsedSlash, SlashParseError, parse_slash


def test_parse_slash_simple():
    assert parse_slash("/help") == ParsedSlash(name="help", args=[])


def test_parse_slash_with_args():
    assert parse_slash("/init dogfood") == ParsedSlash(name="init", args=["dogfood"])


def test_parse_slash_with_quoted_args():
    p = parse_slash('/ticket new --title "set due date"')
    assert p.name == "ticket"
    assert p.args == ["new", "--title", "set due date"]


def test_parse_slash_rejects_non_slash():
    with pytest.raises(SlashParseError, match="not a slash command"):
        parse_slash("init dogfood")


def test_parse_slash_rejects_empty():
    with pytest.raises(SlashParseError, match="empty"):
        parse_slash("/")
    with pytest.raises(SlashParseError, match="empty"):
        parse_slash("/   ")
```

- [ ] **Step 3: Run — expect PASS**

`uv run pytest tests/test_tui_slash.py -v`
Expected: 5 PASS.

- [ ] **Step 4: Build the Now screen with input + scrollback**

```python
# jig/tui/screens/now.py — replace the placeholder
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Input, RichLog

from jig.tui.slash import SlashParseError, parse_slash


class NowScreen(Screen):
    """Active-interaction screen — scrolling transcript + input."""

    DEFAULT_CSS = """
    NowScreen {
        layout: vertical;
    }
    #scrollback {
        height: 1fr;
    }
    #input {
        height: 3;
        dock: bottom;
    }
    """

    def compose(self) -> ComposeResult:
        yield RichLog(id="scrollback", auto_scroll=True, markup=True)
        yield Input(id="input", placeholder="› type a slash command or message")

    async def on_mount(self) -> None:
        self.query_one("#input", Input).focus()
        self.query_one("#scrollback", RichLog).write(
            "[dim]welcome to jig — try /help[/dim]"
        )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        if not line:
            return
        scrollback = self.query_one("#scrollback", RichLog)
        scrollback.write(f"[cyan]›[/cyan] {line}")
        if line.startswith("/"):
            try:
                parsed = parse_slash(line)
            except SlashParseError as exc:
                scrollback.write(f"[red]error:[/red] {exc}")
            else:
                await self._dispatch_slash(parsed)
        else:
            # Phase 3 will route to the concierge agent
            scrollback.write("[dim](free-text — concierge coming in Phase 3)[/dim]")
        event.input.clear()

    async def _dispatch_slash(self, parsed) -> None:
        scrollback = self.query_one("#scrollback", RichLog)
        if parsed.name == "help":
            scrollback.write(
                "[bold]Available commands:[/bold]\n"
                "  /help    — show this message\n"
                "  /status  — daemon + agent status\n"
                "  /quit    — quit the TUI"
            )
        elif parsed.name == "status":
            scrollback.write(
                f"[bold]daemon:[/bold] {self.app.daemon_state.value}\n"
                "[dim]agent details coming in Phase 3[/dim]"
            )
        elif parsed.name == "quit":
            self.app.exit()
        else:
            scrollback.write(
                f"[red]unknown command:[/red] /{parsed.name} "
                "[dim](try /help)[/dim]"
            )
```

- [ ] **Step 5: Test slash dispatch in the TUI**

```python
# tests/test_tui_shell.py — append
@pytest.mark.asyncio
async def test_slash_help_writes_to_scrollback(tmp_path: Path):
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        # Type "/help" + Enter
        await pilot.press("/", "h", "e", "l", "p", "enter")
        scrollback = app.query_one("#scrollback")
        # Textual's RichLog stores written lines; assert "Available" appears
        text = "\n".join(str(line) for line in scrollback.lines)
        assert "Available" in text or "help" in text.lower()
```

- [ ] **Step 6: Run tests**

`uv run pytest tests/test_tui_shell.py tests/test_tui_slash.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add jig/tui/screens/now.py jig/tui/slash.py tests/test_tui_slash.py tests/test_tui_shell.py
git commit -m "feat(tui-now): scrollback + input + slash parser with /help, /status, /quit"
```

### 2.4 Help overlay modal

**Files:**
- Create: `jig/tui/screens/help.py`
- Modify: `jig/tui/app.py`

- [ ] **Step 1: Write the help screen**

```python
# jig/tui/screens/help.py
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center
from textual.screen import ModalScreen
from textual.widgets import Static


_HELP = """\
[bold]Global keys[/bold]

  Tab / Shift+Tab   cycle screens
  1 / 2 / 3 / 4     jump to Now / Tickets / Spec / Events
  ?                 toggle this help
  q / Ctrl+C        quit TUI (daemon keeps running)

[bold]Now screen[/bold]

  / + command       run a slash command
  Enter             submit input
  free text         routed to the concierge agent (Phase 3)

[bold]Slash commands[/bold]

  /help     this overlay
  /status   daemon + agent state
  /quit     quit the TUI

[dim]Press Escape to close.[/dim]
"""


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Center():
            yield Static(_HELP, id="help-text")
```

- [ ] **Step 2: Wire `?` to push the help screen**

```python
# jig/tui/app.py — modify action_help
def action_help(self) -> None:
    from jig.tui.screens.help import HelpScreen
    self.push_screen(HelpScreen())
```

- [ ] **Step 3: Test**

```python
# tests/test_tui_shell.py — append
@pytest.mark.asyncio
async def test_help_overlay_opens_and_closes(tmp_path: Path):
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("question_mark")
        # HelpScreen should be on top of the stack
        from jig.tui.screens.help import HelpScreen
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        assert not isinstance(app.screen, HelpScreen)
```

- [ ] **Step 4: Run tests + manual smoke**

`uv run pytest tests/test_tui_shell.py -v` — expect PASS.
Manual: `jig`, press `?`, see help, press Escape, back to Now.

- [ ] **Step 5: Commit**

```bash
git add jig/tui/screens/help.py jig/tui/app.py tests/test_tui_shell.py
git commit -m "feat(tui): help overlay (?), modal screen with key reference"
```

---

## Phase 3: Now active mode + concierge agent

**What:** Make Now real. Question panels rendered inline (port the rich `Panel` from `prompt_and_post_answers`); the answer flow posts `Answer` thread entries through the daemon. Brief approval flow inline. `/init <name>` runs the existing `run_init` in the daemon, streams events to Now's scrollback. Concierge agent role + free-text dispatch. `--print` mode.

**Why:** The TUI becomes usable for the actual init flow and for ad-hoc operator queries.

**Verify:** End-to-end: `jig init dogfood` via the new TUI produces a brief, approves it, runs spec-gen, picks a template, scaffolds. Manually verified.

### 3.1 `--print` mode

**Files:**
- Modify: `jig/__main__.py`
- Modify: `jig/cli.py` (add `--print` flag handling)
- Create: `tests/test_tui_print_mode.py`

- [ ] **Step 1: Wire the `--print` flag at the entry point**

```python
# jig/__main__.py — modify
import sys

from jig.cli import cli


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--print":
        # `jig --print "/spec capabilities"` → run a single slash command
        # against the daemon, print result, exit.
        if len(args) < 2:
            print("--print requires a slash command argument", file=sys.stderr)
            sys.exit(2)
        from jig.tui.print_mode import run_print

        sys.exit(run_print(args[1]))
    if not args:
        sys.argv = ["jig", "tui"]
    cli()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Implement print-mode**

```python
# jig/tui/print_mode.py
"""--print mode: run a single slash command against the daemon, print
the result to stdout, exit. CI / scripting escape hatch."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from jig.daemon import daemon_paths, daemon_status
from jig.tui.daemon_client import DaemonClient
from jig.tui.slash import SlashParseError, parse_slash


def run_print(command_line: str) -> int:
    """Run a slash command and return an exit code (0 on success)."""
    try:
        parsed = parse_slash(command_line)
    except SlashParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    project_path = Path.cwd()
    status = daemon_status(project_path)
    if not status.running:
        print("error: daemon not running; start it with `jig daemon start`",
              file=sys.stderr)
        return 3

    addr = daemon_paths(project_path).socket_addr_file.read_text().strip()
    return asyncio.run(_dispatch(addr, parsed.name, parsed.args))


async def _dispatch(addr: str, name: str, args: list[str]) -> int:
    client = DaemonClient(url=addr)
    await client.connect()
    try:
        await client.send_command(name, {"args": args})
        async for msg in client.messages():
            if msg.get("type") == "result":
                if msg.get("ok"):
                    payload = msg.get("data")
                    print(json.dumps(payload, indent=2) if isinstance(payload, (dict, list)) else payload)
                    return 0
                else:
                    print(f"error: {msg.get('error', 'unknown')}", file=sys.stderr)
                    return 1
    finally:
        await client.close()
    return 0
```

> **Note for the implementer:** the daemon needs to send `{type: "result", ok: bool, data: ...}` in response to commands. That's added in §3.2 below as part of the command dispatcher.

- [ ] **Step 3: Test parsing with smoke (full integration test in §3.2)**

```python
# tests/test_tui_print_mode.py
import pytest

from jig.tui.print_mode import run_print


def test_run_print_rejects_non_slash(capsys):
    code = run_print("status")  # missing leading /
    assert code == 2
    err = capsys.readouterr().err
    assert "not a slash command" in err
```

- [ ] **Step 4: Run + commit**

`uv run pytest tests/test_tui_print_mode.py -v`
Expected: PASS.

```bash
git add jig/__main__.py jig/tui/print_mode.py tests/test_tui_print_mode.py
git commit -m "feat(tui): --print mode for one-shot slash command execution"
```

### 3.2 Daemon-side command dispatcher

**Files:**
- Modify: `jig/ws_server.py` (extend `_dispatch_command` for new command surface)
- Create: `jig/tui/commands/__init__.py`
- Create: `jig/tui/commands/status.py`
- Create: `tests/test_tui_commands.py`

- [ ] **Step 1: Sketch the command interface**

```python
# jig/tui/commands/__init__.py
"""Slash command handlers — daemon-side implementations.

Each command is an async callable that receives args + access to the
orchestrator's stores and returns a dict ``{ok, data}`` (or ``{ok: False, error}``).
The same handler is used by the TUI input dispatcher and by --print mode.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


CommandHandler = Callable[..., Awaitable[dict[str, Any]]]


_REGISTRY: dict[str, CommandHandler] = {}


def register(name: str):
    def deco(fn: CommandHandler) -> CommandHandler:
        _REGISTRY[name] = fn
        return fn
    return deco


def get_handler(name: str) -> CommandHandler | None:
    return _REGISTRY.get(name)


def known_commands() -> list[str]:
    return sorted(_REGISTRY)


# Import command modules so their @register decorators run at import time.
from jig.tui.commands import status  # noqa: F401
```

- [ ] **Step 2: Write the `status` command (simplest, no orchestrator interaction)**

```python
# jig/tui/commands/status.py
from typing import Any

from jig.tui.commands import register


@register("status")
async def cmd_status(*, args: list[str], orch, project_path) -> dict[str, Any]:
    """`/status` — daemon + agent overview."""
    active_agents = await orch.list_active_agents() if orch else []
    return {
        "ok": True,
        "data": {
            "agents_active": len(active_agents),
            "agents": [{"role": a.role, "ticket": a.ticket_id} for a in active_agents],
        },
    }
```

- [ ] **Step 3: Wire into the WebSocket dispatcher**

```python
# jig/ws_server.py — modify _dispatch_command
from jig.tui.commands import get_handler


async def _dispatch_command(self, websocket, name: str, args: dict) -> None:
    handler = get_handler(name)
    if handler is None:
        await self._safe_send(
            websocket, json.dumps({"type": "result", "ok": False,
                                    "error": f"unknown command: {name}"})
        )
        return
    try:
        result = await handler(
            args=args.get("args", []),
            orch=self._orch,
            project_path=self._project_path,
        )
    except Exception as exc:  # noqa: BLE001
        await self._safe_send(
            websocket, json.dumps({"type": "result", "ok": False,
                                    "error": str(exc)})
        )
        return
    await self._safe_send(
        websocket, json.dumps({"type": "result", **result})
    )
```

> **Note for the implementer:** the existing `_handle_incoming` in `ws_server.py` has legacy command names (`create_ticket`, `comment_on_ticket`, etc.) wired directly. Keep that legacy path for the existing Bun TUI (deleted in Phase 5). The new `command` envelope routes through `_dispatch_command` above.

- [ ] **Step 4: Test the status command end-to-end**

```python
# tests/test_tui_commands.py
import pytest

from jig.tui.commands import get_handler, known_commands


def test_status_command_is_registered():
    assert "status" in known_commands()
    assert get_handler("status") is not None


@pytest.mark.asyncio
async def test_status_command_returns_zero_agents_when_orch_none():
    handler = get_handler("status")
    result = await handler(args=[], orch=None, project_path=None)
    assert result["ok"] is True
    assert result["data"]["agents_active"] == 0
```

- [ ] **Step 5: Manual smoke for --print**

```bash
jig daemon start
jig --print "/status"
# Expected JSON output: {"agents_active": 0, "agents": []}
jig daemon stop
```

- [ ] **Step 6: Commit**

```bash
git add jig/tui/commands/ jig/ws_server.py tests/test_tui_commands.py
git commit -m "feat(tui-commands): handler registry + /status; daemon dispatch wired"
```

### 3.3 Init flow inline in Now

> **NOTE: this task is outlined, not bite-sized.** The existing
> `run_init` is non-trivial — `_cli_emitter`, `prompt_brief_approval`,
> `prompt_and_post_answers`, `prompt_branch_choice`,
> `prompt_sa_confirm`, the rich Console writes, the `click.prompt`
> calls. Refactoring it to support both CLI and TUI cleanly is real
> work that benefits from a focused brainstorm + mini-plan when we
> start it. The four sub-steps below are a starting outline; expect
> to expand each into a full TDD task list at execution time.

**Files:**
- Create: `jig/tui/commands/init.py`
- Modify: `jig/tui/screens/now.py` (handle init events)
- Modify: `jig/init_workflow.py` (refactor to take a prompt handler)

This is the load-bearing task: `/init <name>` runs the existing `run_init` flow inside the daemon, but its rich-rendered output (rules, panels, prompts) needs to render in Now's scrollback instead of stdout.

The cleanest approach: extend `run_init` to take a `console: rich.Console` parameter (defaults to the existing CLI Console). For TUI mode, pass a Console whose `file` is a buffer that gets streamed back to the TUI via WebSocket events. Each "render" becomes a message of type `{type: event, topic: agents, kind: "render", data: {role, content}}`.

The TUI's Now screen subscribes to that and writes the content (with markup) to its `RichLog`.

This is a substantial refactor. Implementer should:
1. Inspect `jig/init_workflow.py`'s `_cli_emitter`, `prompt_brief_approval`, `prompt_and_post_answers`, `run_init`.
2. Identify all `console.print(...)` and `click.echo` / `click.prompt` call sites.
3. Replace direct printing with event emission via the existing `EventEmitter`. Each rendered chunk is an `agent_render` event.
4. Replace `click.prompt` with a request/response over the WebSocket: daemon emits `{kind: "prompt_request", question, options}`, TUI shows it inline, operator types answer, TUI sends `{type: "command", name: "answer_prompt", args: {...}}` back.

This is too large for a single bite-sized step. Break into 4 sub-tasks:

- [ ] **Step 1: Add `console` parameter to `run_init` + plumb through**

Modify `run_init` signature:
```python
async def run_init(*, name: str, force: bool, console=None) -> None:
```

When `console` is None, use the existing default (sys.stdout). When provided, all `console.print` / `_cli_emitter` output goes through it. This is a refactor — should not change CLI behavior.

Run existing init tests to verify no regression.

```bash
uv run pytest tests/test_init_workflow_e2e.py tests/test_init_workflow.py -v
```

Commit.

- [ ] **Step 2: Extract the prompt request/response into an injectable handler**

Create `jig/init_workflow_prompts.py` with a `PromptHandler` protocol:

```python
class PromptHandler(Protocol):
    async def ask_brief_approval(self, brief_md: str) -> BriefApprovalChoice: ...
    async def ask_branch(self) -> BranchChoice: ...
    async def ask_confirm(self, *, template: str, rationale: str) -> ConfirmChoice: ...
    async def ask_answer(self, question: str, asker: str) -> str: ...
```

Refactor `run_init` and helpers to take a `prompt_handler: PromptHandler`. The default is a CLI implementation that uses click.prompt + rich. The TUI implementation translates each method to a WebSocket round-trip.

Run init tests; commit.

- [ ] **Step 3: Implement the TUI-side prompt handler**

```python
# jig/tui/init_prompts.py
class TuiPromptHandler:
    def __init__(self, ws_send, ws_receive):
        self._send = ws_send
        self._receive = ws_receive

    async def ask_brief_approval(self, brief_md: str) -> BriefApprovalChoice:
        await self._send({
            "type": "event", "topic": "agents", "kind": "prompt_request",
            "data": {"prompt": "brief_approval", "brief_md": brief_md},
        })
        reply = await self._receive("prompt_reply")
        return BriefApprovalChoice.parse(reply["choice"])
    # ... etc for branch, confirm, answer
```

The TUI's Now screen renders the brief in a Panel, prompts the operator via the input field, and sends `prompt_reply` back.

Test with a mocked send/receive pair.

Commit.

- [ ] **Step 4: Wire `/init` as a slash command**

```python
# jig/tui/commands/init.py
from jig.init_workflow import run_init
from jig.tui.commands import register


@register("init")
async def cmd_init(*, args: list[str], orch, project_path, ws_handle=None) -> dict:
    if not args:
        return {"ok": False, "error": "/init requires a project name"}
    name = args[0]
    handler = TuiPromptHandler(...)  # wires to ws_handle for prompt round-trips
    await run_init(name=name, force=False, prompt_handler=handler)
    return {"ok": True, "data": {"name": name}}
```

Manual smoke:

```bash
jig daemon start
jig
# In TUI: /init dogfood
# Answer questions in input field; brief approval renders inline; etc.
```

If end-to-end works, commit. If not, debug per phase risk #1 (Textual + asyncio).

### 3.4 Concierge agent

**Files:**
- Create: `jig/defaults/roles/concierge.yaml`
- Create: `jig/tui/commands/concierge.py`
- Modify: `jig/tui/screens/now.py` (free-text input → /concierge dispatch)

- [ ] **Step 1: Define the concierge role**

```yaml
# jig/defaults/roles/concierge.yaml
role: concierge
phase_prompt: >
  You are the concierge agent for the jig TUI. The operator types
  free-text questions or requests; you either answer conversationally
  or propose a structured action (slash command) for confirmation.


  ## Tool surface

  Read tools (use freely):
  - spec_list_capabilities, spec_get_capability, spec_list_non_goals,
    spec_resolve_uri
  - list_tickets, read_ticket, read_comments
  - recent_events (last N bus messages)

  Action proposal:
  - propose_slash(name, args, summary) — your only "write" — proposes
    a slash command for the operator to confirm. NEVER execute writes
    directly. Always propose.


  ## Style

  - Concise. The operator is in a TUI; long prose wastes screen.
  - Format with rich markup where helpful (bold, dim).
  - When you propose an action, summarize what it'll do in one line.
allowed_tools:
  - spec_list_capabilities
  - spec_get_capability
  - spec_list_non_goals
  - spec_resolve_uri
  - list_tickets
  - read_ticket
  - read_comments
  - recent_events
  - propose_slash
allowed_mcps: []
strict_tools: true
default_context: []
```

- [ ] **Step 2: Implement `propose_slash` MCP tool**

This is a new tool — probably in a new `jig/concierge_mcp.py` module. The handler doesn't execute anything; it returns the proposal as data which the TUI renders for confirmation. The TUI executes via the regular slash dispatcher on confirm.

- [ ] **Step 3: Wire concierge spawn into a slash command**

```python
# jig/tui/commands/concierge.py
@register("concierge")
async def cmd_concierge(*, args, orch, project_path, ws_handle=None) -> dict:
    user_input = " ".join(args)
    # Spawn the concierge agent via the existing agent infrastructure
    # ... (similar pattern to run_po_conversation but headless)
    # Stream agent_text events to the TUI; final response or proposal
    # is the result.
```

- [ ] **Step 4: Free-text input on Now → /concierge**

In `NowScreen.on_input_submitted`, when the line doesn't start with `/`, dispatch as `await self._dispatch_slash(ParsedSlash(name="concierge", args=[line]))`.

- [ ] **Step 5: Test + commit**

Manual: type "what's the status?" in Now; concierge runs, replies inline.

---

## Phase 4: Tickets, Spec, Events screens

> **NOTE: these tasks are outlined, not bite-sized.** Each screen is
> a substantive UI task that benefits from looking at the live data
> shapes, the Bun TUI's existing implementation, and Textual's
> widget catalog when starting. Outlines below give the shape; expect
> to expand each into a full TDD task list (~5-15 steps each) at
> execution time.

Order: Tickets first (highest-traffic), Spec, Events.

### 4.1 Tickets screen — list + detail

Build `jig/tui/screens/tickets.py` with a left ListView of tickets (subscribed to the `tickets` topic) and a right detail pane (Static rendering the focused ticket). Hotkeys: `j`/`k` to navigate, `Enter` to focus detail. Subscribe at mount; rebuild list when snapshot/event arrives.

### 4.2 Tickets — board view toggle

Add `b` hotkey to swap layout. New `BoardView` widget with one column per status. Same data, different rendering.

### 4.3 Tickets — new + update actions

Inline form for `/ticket new` (or `n` hotkey on Tickets). Form widget composes Input fields; on submit, dispatch `/ticket new --title ... --size ...`. Same for `e` to edit (status, assignee).

### 4.4 Spec screen — capabilities + non-goals

`jig/tui/screens/spec.py` with master/detail. Subscribe to `spec` topic; render capabilities grouped by state. Detail pane shows behaviors + AC. Modal for raw YAML (`r`); modal for rendered brief (`b`).

### 4.5 Events screen — tail + filter + follow

`jig/tui/screens/events.py` with a scrolling RichLog. Subscribe to `events` topic. Hotkeys: `f` (filter cycle), `F` (follow toggle), `/` (search), `Enter` (modal with full payload).

---

## Phase 5: Cutover (same PR as Phase 4 completion)

**Files:**
- Delete: `tui/` directory (Bun TUI source)
- Delete: `tui/bun.lock`
- Delete: `tui/package.json`
- Modify: `pyproject.toml` (promote `textual` to runtime dep)
- Modify: `docs/` (drop `cd tui && bun install` instructions)
- Modify: `Dockerfile` (drop Bun install step)

- [ ] **Step 1: Verify the new TUI covers all flows you actually use**

Smoke test:
```bash
jig daemon start
jig
# Run through: /init dogfood, /spec capabilities, /ticket new,
# /status, free-text "what's open?"
# Switch tabs: 1, 2, 3, 4
# Help: ?
# Quit: q
```

- [ ] **Step 2: Delete the Bun TUI**

```bash
git rm -r tui/
```

- [ ] **Step 3: Promote `textual` to runtime dep**

Move `"textual>=0.80"` from `[dependency-groups].dev` to `[project].dependencies` in `pyproject.toml`. `uv sync`.

- [ ] **Step 4: Update Dockerfile**

The Dockerfile (per CLAUDE.md: "Container image (Python 3.12 + Node 22 + bwrap)") installs Node + Bun for the Bun TUI. Drop those steps; keep only Python 3.12 + bwrap.

- [ ] **Step 5: Update docs**

```bash
grep -rn "bun install\|cd tui\|gridland" docs/ README.md CLAUDE.md 2>/dev/null
```

Replace each occurrence with the new launch instructions (`jig daemon start && jig`).

- [ ] **Step 6: Update CLAUDE.md project structure**

```
jig/                  # Python package — orchestrator, agents, stores, MCP, TUI
tests/                # pytest suite (async)
templates/            # Project init templates
Dockerfile            # Container image (Python 3.12 + bwrap)
```

(Drop the `tui/` line.)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
feat(tui): cutover from Bun TUI to Python Textual

Deletes tui/, package.json, bun.lock; promotes textual to runtime dep;
updates Dockerfile to drop Node/Bun; updates docs to drop the
cd tui && bun install step.

The new Textual TUI covers Init / Tickets / Spec / Events flows plus
the always-on concierge agent. CI / scripting uses jig --print.

EOF
)"
```

---

## Rollback

This is a multi-phase refactor of a developer-facing tool. Rollback per phase:

- Phase 1 (daemon): `git revert` the daemon commits; `jig start` still works in foreground; no impact on existing TUI which still connects via WebSocket.
- Phase 2 (TUI shell): `git revert` removes the new TUI but leaves daemon refactor in place; existing Bun TUI keeps working.
- Phase 3 (init in TUI): partially revertable — the prompt-handler refactor in `init_workflow.py` is invasive. Reverting Phase 3 may require also reverting Phase 1 init refactor steps.
- Phase 4 (other screens): each screen is independent; revert per screen.
- Phase 5 (cutover): hardest to revert because it deletes the Bun TUI. Don't merge Phase 5 until you've used the new TUI for a few days.

For each phase, the `--legacy` route doesn't exist (sole-user, no flag), but Phases 1-4 don't break the Bun TUI — it's launched directly via `cd tui && bun run src/main.tsx`. Until Phase 5, both TUIs work in parallel.

## Out of scope for this plan

- Multi-tenant or remote daemon support.
- Web view of the same daemon.
- Auth / transport security beyond a local Unix socket.
- Vim-mode key bindings.
- Notification surfaces (system tray, sound).
- Free-text concierge dispatch for *every* operation — v1 limits to a curated set of safe-to-execute actions.

## Change log

- 2026-04-28: Initial draft (brent)
