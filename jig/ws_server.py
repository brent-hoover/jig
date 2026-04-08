"""WebSocket server for broadcasting events to TUI clients."""

import asyncio

import websockets
from websockets.asyncio.server import serve, ServerConnection

from jig.events import EventEmitter, JigEvent


class WebSocketServer:
    def __init__(self, emitter: EventEmitter, host: str = "127.0.0.1", port: int = 9100) -> None:
        self._emitter = emitter
        self._host = host
        self._port = port
        self._server = None
        self._relay_task = None
        self._clients: set[ServerConnection] = set()
        self._queue = emitter.subscribe()

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
        self._clients.add(websocket)
        try:
            async for _ in websocket:
                pass  # We don't expect messages from clients in v1
        finally:
            self._clients.discard(websocket)

    async def _relay_events(self) -> None:
        while True:
            event = await self._queue.get()
            message = event.to_json()
            for client in list(self._clients):
                try:
                    await client.send(message)
                except websockets.ConnectionClosed:
                    self._clients.discard(client)
