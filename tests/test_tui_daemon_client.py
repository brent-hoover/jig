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


@pytest.mark.asyncio
async def test_subscribe_raises_when_not_connected():
    c = DaemonClient(url="ws://localhost:9999")
    with pytest.raises(RuntimeError, match="not connected"):
        await c.subscribe(["tickets"])
