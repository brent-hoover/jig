"""WebSocket server for broadcasting events to TUI clients."""

import asyncio
import json
from typing import TYPE_CHECKING

import websockets
from websockets.asyncio.server import serve, ServerConnection

from jig.events import EventEmitter
from jig.ticket_mcp import (
    handle_create_ticket,
    handle_comment_on_ticket,
    handle_update_ticket,
)

if TYPE_CHECKING:
    from jig.orchestrator import Orchestrator


class WebSocketServer:
    def __init__(
        self,
        emitter: EventEmitter,
        host: str = "127.0.0.1",
        port: int = 9100,
        orchestrator: "Orchestrator | None" = None,
    ) -> None:
        self._emitter = emitter
        self._host = host
        self._port = port
        self._orch = orchestrator
        self._server = None
        self._relay_task = None
        self._clients: set[ServerConnection] = set()
        self._queue = emitter.subscribe()
        self._history: list[str] = []

    @property
    def port(self) -> int:
        if self._server is not None:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    async def start(self) -> None:
        self._server = await serve(
            self._handle_client,
            self._host,
            self._port,
        )
        self._relay_task = asyncio.create_task(self._relay_events())

    async def stop(self) -> None:
        if self._relay_task:
            self._relay_task.cancel()
            try:
                await self._relay_task
            except asyncio.CancelledError:
                pass
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._emitter.unsubscribe(self._queue)

    async def _handle_client(self, websocket: ServerConnection) -> None:
        # Replay event history to late-joining clients
        for message in self._history:
            try:
                await websocket.send(message)
            except websockets.ConnectionClosed:
                return
        self._clients.add(websocket)
        try:
            async for raw in websocket:
                await self._handle_incoming(websocket, raw)
        finally:
            self._clients.discard(websocket)

    async def _safe_send(self, websocket: ServerConnection, message: str) -> None:
        """Send a reply, swallowing ConnectionClosed so a dying client
        doesn't bubble a traceback through ``_handle_client``."""
        try:
            await websocket.send(message)
        except websockets.ConnectionClosed:
            return

    async def _handle_incoming(self, websocket: ServerConnection, raw: str) -> None:
        if self._orch is None:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await self._safe_send(websocket, json.dumps({"ok": False, "error": "bad json"}))
            return

        command = payload.get("command")
        args = payload.get("args", {})

        try:
            if command == "create_ticket":
                tid = await handle_create_ticket(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    bus=self._orch.bus,
                    sender="user",
                    args=args,
                )
                await self._safe_send(websocket, json.dumps({"ok": True, "ticket_id": tid}))
            elif command == "comment_on_ticket":
                cid = await handle_comment_on_ticket(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    bus=self._orch.bus,
                    sender="user",
                    sender_cfg=None,
                    args=args,
                )
                await self._safe_send(websocket, json.dumps({"ok": True, "comment_id": cid}))
            elif command == "update_ticket":
                updated = await handle_update_ticket(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    bus=self._orch.bus,
                    sender="user",
                    args=args,
                )
                await self._safe_send(
                    websocket, json.dumps({"ok": True, "status": updated.status.value})
                )
            else:
                await self._safe_send(
                    websocket, json.dumps({"ok": False, "error": f"unknown command {command}"})
                )
        except Exception as exc:
            await self._safe_send(websocket, json.dumps({"ok": False, "error": str(exc)}))

    async def _relay_events(self) -> None:
        while True:
            event = await self._queue.get()
            message = event.to_json()
            self._history.append(message)
            for client in list(self._clients):
                try:
                    await client.send(message)
                except websockets.ConnectionClosed:
                    self._clients.discard(client)
