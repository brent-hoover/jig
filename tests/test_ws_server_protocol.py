"""Tests for the typed snapshot + event protocol added in Task 1.2."""

import asyncio
import json

import pytest
import websockets

from jig.events import EventEmitter, JigEvent
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ticket import Ticket, WorkType
from jig.ws_server import WebSocketServer, event_envelope, snapshot_envelope


# ---------------------------------------------------------------------------
# Envelope helper tests
# ---------------------------------------------------------------------------


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


def test_snapshot_envelope_rejects_unknown_topic():
    with pytest.raises(ValueError, match="unknown topic"):
        snapshot_envelope("not-a-topic", [])


def test_event_envelope_rejects_unknown_topic():
    with pytest.raises(ValueError, match="unknown topic"):
        event_envelope("not-a-topic", "updated", {})


# ---------------------------------------------------------------------------
# Subscribe + snapshot delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_to_tickets_yields_snapshot(tmp_path):
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()

    # Create a ticket so the snapshot is non-empty
    await orch.tickets.create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )

    emitter = EventEmitter()
    server = WebSocketServer(
        emitter,
        port=0,
        orchestrator=orch,
        project_path=tmp_path,
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
        await orch.shutdown()


@pytest.mark.asyncio
async def test_subscribe_unknown_topic_returns_error(tmp_path):
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()

    emitter = EventEmitter()
    server = WebSocketServer(
        emitter,
        port=0,
        orchestrator=orch,
        project_path=tmp_path,
    )
    await server.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{server.port}") as client:
            await client.send(json.dumps({"type": "subscribe", "topics": ["bogus"]}))
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            msg = json.loads(raw)
            assert msg["type"] == "error"
            assert msg["topic"] == "bogus"
    finally:
        await server.stop()
        await orch.shutdown()


@pytest.mark.asyncio
async def test_legacy_command_still_works_after_new_protocol(tmp_path):
    """Bun TUI's bare command shape must still be dispatched correctly."""
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()

    emitter = EventEmitter()
    server = WebSocketServer(
        emitter,
        port=0,
        orchestrator=orch,
        project_path=tmp_path,
    )
    await server.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{server.port}") as client:
            await client.send(json.dumps({"command": "list_tickets", "args": {}}))
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            msg = json.loads(raw)
            assert msg["ok"] is True
            assert "tickets" in msg
    finally:
        await server.stop()
        await orch.shutdown()


# ---------------------------------------------------------------------------
# Fix #1 — history replay gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_typed_subscriber_does_not_get_raw_history(tmp_path):
    """A client that subscribes must never receive raw history frames."""
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()

    emitter = EventEmitter()
    server = WebSocketServer(emitter, port=0, orchestrator=orch, project_path=tmp_path)
    await server.start()
    try:
        url = f"ws://127.0.0.1:{server.port}"
        # Seed some history by emitting events before the client connects.
        await emitter.emit(JigEvent(type="phase_started", data={"phase": "spec"}))
        await asyncio.sleep(0.05)  # let relay_events process

        async with websockets.connect(url) as client:
            await client.send(json.dumps({"type": "subscribe", "topics": ["tickets"]}))
            # Drain all available messages (snapshot + any history replays).
            received = []
            while True:
                try:
                    raw = await asyncio.wait_for(client.recv(), timeout=0.3)
                    received.append(json.loads(raw))
                except asyncio.TimeoutError:
                    break

            # All messages must be typed envelopes — no raw history frames.
            for msg in received:
                assert "type" in msg, f"missing 'type' key in {msg}"
                assert msg["type"] in ("snapshot", "event", "error"), (
                    f"unexpected raw frame: {msg}"
                )
    finally:
        await server.stop()
        await orch.shutdown()


@pytest.mark.asyncio
async def test_typed_subscriber_filtered_by_topic(tmp_path):
    """A client subscribed to 'tickets' must NOT receive 'agents' events."""
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()

    emitter = EventEmitter()
    server = WebSocketServer(emitter, port=0, orchestrator=orch, project_path=tmp_path)
    await server.start()
    try:
        url = f"ws://127.0.0.1:{server.port}"
        async with websockets.connect(url) as client:
            # Subscribe to tickets only.
            await client.send(json.dumps({"type": "subscribe", "topics": ["tickets"]}))
            # Consume the snapshot.
            await asyncio.wait_for(client.recv(), timeout=2.0)

            # Emit an agent_text event — classifies as 'agents' topic.
            await emitter.emit(JigEvent(type="agent_text", data={"text": "hello"}))
            await asyncio.sleep(0.05)

            # No message should arrive — agent event not in subscription.
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(client.recv(), timeout=0.3)
    finally:
        await server.stop()
        await orch.shutdown()


@pytest.mark.asyncio
async def test_command_dispatch_status_returns_typed_result(tmp_path):
    """Send {type: command, name: status} → expect {type: result, ok: true, data: ...}

    Uses a fake websocket object to avoid needing a live server for this unit.
    """
    from jig.ws_server import WebSocketServer
    from jig.events import EventEmitter

    class FakeWebSocket:
        def __init__(self):
            self.sent: list[str] = []

        async def send(self, msg: str) -> None:
            self.sent.append(msg)

    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    emitter = EventEmitter()
    server = WebSocketServer(emitter, port=0, project_path=tmp_path)

    ws = FakeWebSocket()
    await server._dispatch_command(ws, "status", {"args": []})

    assert len(ws.sent) == 1
    reply = json.loads(ws.sent[0])
    assert reply["type"] == "result"
    assert reply["ok"] is True
    assert "agents_active" in reply["data"]


@pytest.mark.asyncio
async def test_command_dispatch_unknown_returns_error(tmp_path):
    """Unknown command name returns {type: result, ok: false, error: ...}."""
    from jig.ws_server import WebSocketServer
    from jig.events import EventEmitter

    class FakeWebSocket:
        def __init__(self):
            self.sent: list[str] = []

        async def send(self, msg: str) -> None:
            self.sent.append(msg)

    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    emitter = EventEmitter()
    server = WebSocketServer(emitter, port=0, project_path=tmp_path)

    ws = FakeWebSocket()
    await server._dispatch_command(ws, "not-a-real-command", {})

    assert len(ws.sent) == 1
    reply = json.loads(ws.sent[0])
    assert reply["type"] == "result"
    assert reply["ok"] is False
    assert "unknown command" in reply["error"]


@pytest.mark.asyncio
async def test_command_dispatch_via_wire_protocol(tmp_path):
    """Full round-trip: send {type: command, name: status} over websocket."""
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()

    emitter = EventEmitter()
    server = WebSocketServer(emitter, port=0, orchestrator=orch, project_path=tmp_path)
    await server.start()
    try:
        async with websockets.connect(f"ws://127.0.0.1:{server.port}") as client:
            await client.send(
                json.dumps({"type": "command", "name": "status", "args": []})
            )
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            msg = json.loads(raw)
            assert msg["type"] == "result"
            assert msg["ok"] is True
            assert "agents_active" in msg["data"]
    finally:
        await server.stop()
        await orch.shutdown()


def test_prompts_topic_is_valid():
    from jig.ws_server import _VALID_TOPICS

    assert "prompts" in _VALID_TOPICS


@pytest.mark.asyncio
async def test_prompt_reply_command_resolves_pending_prompt():
    """prompt_reply via _dispatch_command resolves a registered Future."""
    from jig.events import EventEmitter
    from jig.ws_server import WebSocketServer

    server = WebSocketServer(emitter=EventEmitter(), port=0, orchestrator=None)
    prompt_id, future = server.prompt_registry.register()

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, msg: str) -> None:
            self.sent.append(msg)

    fake_ws = FakeWS()
    await server._dispatch_command(
        fake_ws, "prompt_reply", {"args": [prompt_id, "the answer"]}
    )

    assert await asyncio.wait_for(future, timeout=1) == "the answer"
    # Reply envelope wrote ok=True back
    assert any('"ok": true' in s for s in fake_ws.sent)


@pytest.mark.asyncio
async def test_prompt_reply_unknown_id_returns_error():
    from jig.events import EventEmitter
    from jig.ws_server import WebSocketServer

    server = WebSocketServer(emitter=EventEmitter(), port=0, orchestrator=None)

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, msg: str) -> None:
            self.sent.append(msg)

    fake_ws = FakeWS()
    await server._dispatch_command(
        fake_ws, "prompt_reply", {"args": ["definitely-not-a-real-id", "x"]}
    )
    assert any('"ok": false' in s for s in fake_ws.sent)
    assert any("no pending prompt" in s for s in fake_ws.sent)


@pytest.mark.asyncio
async def test_legacy_client_still_gets_history_replay(tmp_path):
    """Legacy client sending a bare command receives history replay first."""
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()

    emitter = EventEmitter()
    server = WebSocketServer(emitter, port=0, orchestrator=orch, project_path=tmp_path)
    await server.start()
    try:
        url = f"ws://127.0.0.1:{server.port}"
        # Seed history with two events before the client connects.
        await emitter.emit(JigEvent(type="phase_started", data={"phase": "spec"}))
        await emitter.emit(JigEvent(type="workflow_started", data={"issue": "t1"}))
        await asyncio.sleep(0.05)  # let relay_events process

        async with websockets.connect(url) as client:
            # Send a legacy bare command.
            await client.send(json.dumps({"command": "list_tickets", "args": {}}))

            # Collect all responses within a window.
            received = []
            while True:
                try:
                    raw = await asyncio.wait_for(client.recv(), timeout=0.5)
                    received.append(json.loads(raw))
                except asyncio.TimeoutError:
                    break

            types = [m.get("type") for m in received]
            # History frames should appear (phase_started, workflow_started).
            assert "phase_started" in types
            assert "workflow_started" in types
            # The command response (ok: True) should also be there.
            assert any(m.get("ok") is True for m in received)
    finally:
        await server.stop()
        await orch.shutdown()


def test_classify_event_maps_agent_render_to_agents_topic():
    from jig.events import EventEmitter, JigEvent
    from jig.ws_server import WebSocketServer

    server = WebSocketServer(emitter=EventEmitter(), port=0, orchestrator=None)
    classified = server._classify_event(
        JigEvent(type="agent_render", data={"content": "hi"})
    )
    assert classified == {
        "type": "event",
        "topic": "agents",
        "kind": "render",
        "data": {"content": "hi"},
    }


def test_classify_event_maps_prompt_request_to_prompts_topic():
    from jig.events import EventEmitter, JigEvent
    from jig.ws_server import WebSocketServer

    server = WebSocketServer(emitter=EventEmitter(), port=0, orchestrator=None)
    classified = server._classify_event(
        JigEvent(type="prompt_request", data={"prompt_id": "abc", "prompt_type": "x"})
    )
    assert classified["type"] == "event"
    assert classified["topic"] == "prompts"
    assert classified["kind"] == "request"
    assert classified["data"]["prompt_id"] == "abc"


@pytest.mark.asyncio
async def test_subscribe_snapshots_survive_unconfigured_orchestrator(tmp_path):
    """When the orchestrator has no project loaded yet (unconfigured mode),
    subscribing to topics that depend on stores (tickets, events) must not
    crash the WS handler with AttributeError on `None.list_all()` etc.

    Regression: the old code blindly called self._orch.tickets.list_all()
    even when self._orch.tickets was None, causing the daemon to send
    1011 (internal error) and the TUI to loop in `reconnecting` state.
    """
    from jig.events import EventEmitter
    from jig.orchestrator import Orchestrator
    from jig.ws_server import WebSocketServer

    emitter = EventEmitter()
    orch = Orchestrator(project_path=tmp_path, emitter=emitter)
    await orch.startup()  # unconfigured mode (no .jig/config.yaml here)
    assert orch.is_configured is False
    assert orch.tickets is None

    server = WebSocketServer(
        emitter=emitter, port=0, orchestrator=orch, project_path=tmp_path
    )
    # Each of these used to AttributeError; now they return safe empties.
    assert await server._build_snapshot("tickets") == []
    assert await server._build_snapshot("events") == []
    assert await server._build_snapshot("agents") == []
    assert await server._build_snapshot("threads") == []
    assert await server._build_snapshot("prompts") == []
    assert await server._build_snapshot("spec") is None
    await orch.shutdown()


@pytest.mark.asyncio
async def test_long_running_command_does_not_block_subsequent_messages(tmp_path):
    """Regression: cmd_init blocks awaiting a prompt round-trip that needs
    a SECOND command (prompt_reply) to come through the same WS connection.
    If the read loop awaits the dispatch inline, the second command can
    never be read → deadlock. Read loop must spawn dispatches as tasks.
    """
    import asyncio
    import websockets
    from jig.events import EventEmitter
    from jig.ws_server import WebSocketServer
    from jig.tui.commands import _REGISTRY

    # Register a command that awaits a future the SECOND command resolves.
    fut: asyncio.Future = asyncio.get_running_loop().create_future()
    second_command_ran = asyncio.Event()

    async def cmd_blocker(*, args, **kwargs):
        # Block until cmd_unblock runs. If the read loop is serialized,
        # this deadlocks (cmd_unblock can't run until cmd_blocker returns).
        await asyncio.wait_for(fut, timeout=2.0)
        return {"ok": True, "data": "unblocked"}

    async def cmd_unblock(*, args, **kwargs):
        if not fut.done():
            fut.set_result(None)
        second_command_ran.set()
        return {"ok": True}

    # Patch the registry directly (no @register dance).
    _REGISTRY["__test_blocker"] = cmd_blocker
    _REGISTRY["__test_unblock"] = cmd_unblock
    try:
        server = WebSocketServer(emitter=EventEmitter(), port=0, orchestrator=None)
        await server.start()
        try:
            port = server.port
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                # Send the blocker first
                await ws.send(
                    json.dumps(
                        {"type": "command", "name": "__test_blocker", "args": {}}
                    )
                )
                # Give it a moment to enter the await
                await asyncio.sleep(0.1)
                # Send the unblocker — read loop MUST process it even though
                # blocker hasn't returned yet
                await ws.send(
                    json.dumps(
                        {"type": "command", "name": "__test_unblock", "args": {}}
                    )
                )
                # If the read loop is serialized this never sets
                await asyncio.wait_for(second_command_ran.wait(), timeout=2.0)
        finally:
            await server.stop()
    finally:
        _REGISTRY.pop("__test_blocker", None)
        _REGISTRY.pop("__test_unblock", None)
