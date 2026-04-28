"""WebSocket client to the jig daemon.

Wraps the websockets library in a simple async API the TUI uses to
subscribe to topics, send commands, and receive snapshots/events.
Auto-reconnects on disconnect with exponential backoff.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

import websockets
from websockets.asyncio.client import ClientConnection, connect


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class DaemonClient:
    url: str
    state: ConnectionState = ConnectionState.DISCONNECTED
    _ws: ClientConnection | None = field(default=None, repr=False)
    _reconnect_delay: float = 1.0
    _max_delay: float = 30.0

    async def connect(self) -> None:
        self.state = ConnectionState.CONNECTING
        self._ws = await connect(self.url, ping_interval=20)
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
        on_message: Callable[[dict], Awaitable[None]],
        on_state_change: Callable[[ConnectionState], None],
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
                self.state = ConnectionState.RECONNECTING
                on_state_change(self.state)
                await asyncio.sleep(1)
