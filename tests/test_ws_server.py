import asyncio
import json

import pytest
import websockets

from jig.events import EventEmitter, JigEvent
from jig.ws_server import WebSocketServer


class TestWebSocketServer:
    @pytest.fixture
    async def server(self):
        emitter = EventEmitter()
        ws_server = WebSocketServer(emitter, host="127.0.0.1", port=0)
        await ws_server.start()
        yield ws_server, emitter
        await ws_server.stop()

    async def test_client_connects(self, server):
        ws_server, emitter = server
        async with websockets.connect(f"ws://127.0.0.1:{ws_server.port}") as ws:
            from websockets.protocol import State
            assert ws.state is State.OPEN

    async def test_client_receives_event(self, server):
        ws_server, emitter = server
        async with websockets.connect(f"ws://127.0.0.1:{ws_server.port}") as ws:
            # Small delay to let the client handler register
            await asyncio.sleep(0.1)
            event = JigEvent(type="phase_started", data={"phase": "spec"})
            await emitter.emit(event)
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            parsed = json.loads(msg)
            assert parsed["type"] == "phase_started"
            assert parsed["data"]["phase"] == "spec"

    async def test_multiple_clients(self, server):
        ws_server, emitter = server
        async with websockets.connect(f"ws://127.0.0.1:{ws_server.port}") as ws1:
            async with websockets.connect(f"ws://127.0.0.1:{ws_server.port}") as ws2:
                await asyncio.sleep(0.1)
                event = JigEvent(type="workflow_started", data={"issue": "test"})
                await emitter.emit(event)
                msg1 = await asyncio.wait_for(ws1.recv(), timeout=2.0)
                msg2 = await asyncio.wait_for(ws2.recv(), timeout=2.0)
                assert json.loads(msg1)["type"] == "workflow_started"
                assert json.loads(msg2)["type"] == "workflow_started"
