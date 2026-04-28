import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from jig.tui.daemon_client import ConnectionState, DaemonClient


def make_client(url: str = "ws://localhost:9999", **kwargs) -> DaemonClient:
    return DaemonClient(addr_provider=lambda: url, **kwargs)


def test_daemon_client_starts_disconnected():
    c = make_client()
    assert c.state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_send_command_raises_when_not_connected():
    c = make_client()
    with pytest.raises(RuntimeError, match="not connected"):
        await c.send_command("init", {"name": "x"})


@pytest.mark.asyncio
async def test_subscribe_raises_when_not_connected():
    c = make_client()
    with pytest.raises(RuntimeError, match="not connected"):
        await c.subscribe(["tickets"])


# ---------------------------------------------------------------------------
# run_with_reconnect tests
# ---------------------------------------------------------------------------


def _make_mock_ws(messages: list[dict] | None = None):
    """Return a fake ClientConnection that yields the given messages then closes."""
    import json

    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()

    async def _aiter(self):
        for m in (messages or []):
            yield json.dumps(m)

    ws.__aiter__ = _aiter
    return ws


@pytest.mark.asyncio
async def test_run_with_reconnect_survives_unexpected_exception():
    """Worker must not raise when connect raises an unexpected exception."""
    attempt = 0
    states: list[ConnectionState] = []
    connected_event = asyncio.Event()

    async def fake_connect(url, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise RuntimeError("boom")
        # Second attempt: return a ws that yields nothing and closes cleanly.
        ws = _make_mock_ws([])
        connected_event.set()
        return ws

    client = DaemonClient(
        addr_provider=lambda: "ws://localhost:9999",
        _max_delay=0.05,
        connect_factory=fake_connect,
    )
    # Start with a tiny backoff so the second attempt runs quickly.
    client._reconnect_delay = 0.05

    def on_state(s: ConnectionState) -> None:
        states.append(s)

    async def on_message(msg: dict) -> None:
        pass

    async def run_until_second_connect():
        task = asyncio.create_task(
            client.run_with_reconnect(on_message, on_state, ["tickets"])
        )
        # Wait until the second connect attempt fires.
        await asyncio.wait_for(connected_event.wait(), timeout=2.0)
        # Give it a moment to update state.
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    await run_until_second_connect()

    # Should have gone through: RECONNECTING (after fail) + CONNECTING or CONNECTED (second try)
    assert ConnectionState.RECONNECTING in states
    assert ConnectionState.CONNECTING in states or ConnectionState.CONNECTED in states
    assert attempt >= 2


@pytest.mark.asyncio
async def test_run_with_reconnect_resets_backoff_on_success():
    """_reconnect_delay resets to 1.0 after a successful subscribe."""
    call_count = 0
    # Signal fires when the second (successful) connect call returns.
    second_connected = asyncio.Event()

    async def fake_connect(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First: fail to exercise the backoff path.
            raise OSError("refused")
        # Second: succeed — ws that yields nothing so messages() returns promptly.
        ws = _make_mock_ws([])
        second_connected.set()
        return ws

    client = DaemonClient(
        addr_provider=lambda: "ws://localhost:9999",
        _max_delay=0.1,
        connect_factory=fake_connect,
    )
    # Crank initial backoff down so the retry fires in < 100 ms.
    client._reconnect_delay = 0.05

    states: list[ConnectionState] = []

    def on_state(s: ConnectionState) -> None:
        states.append(s)

    async def run_until_second_connect():
        task = asyncio.create_task(
            client.run_with_reconnect(lambda m: asyncio.sleep(0), on_state, ["tickets"])
        )
        # Wait until the second connect attempt fires and subscribe runs.
        await asyncio.wait_for(second_connected.wait(), timeout=2.0)
        # A tiny sleep lets subscribe + the backoff-reset line execute.
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    await run_until_second_connect()

    # After a successful subscribe, _reconnect_delay is reset to 1.0 regardless
    # of what it was before. The _max_delay cap (0.1) only limits *further* bumps.
    # We started at 0.05 and the reset must bring it back to exactly 1.0.
    assert client._reconnect_delay == 1.0
