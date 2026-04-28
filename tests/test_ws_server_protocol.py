"""Tests for the typed snapshot + event protocol added in Task 1.2."""

import asyncio
import json

import pytest
import websockets

from jig.events import EventEmitter
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
