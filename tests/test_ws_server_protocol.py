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
        emitter, port=0, orchestrator=orch, project_path=tmp_path,
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
        emitter, port=0, orchestrator=orch, project_path=tmp_path,
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
        emitter, port=0, orchestrator=orch, project_path=tmp_path,
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
            await client.send(json.dumps({"type": "command", "name": "status", "args": []}))
            raw = await asyncio.wait_for(client.recv(), timeout=2.0)
            msg = json.loads(raw)
            assert msg["type"] == "result"
            assert msg["ok"] is True
            assert "agents_active" in msg["data"]
    finally:
        await server.stop()
        await orch.shutdown()


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
