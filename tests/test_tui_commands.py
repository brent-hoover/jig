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


@pytest.mark.asyncio
async def test_get_handler_returns_none_for_unknown():
    assert get_handler("definitely-not-a-command") is None
