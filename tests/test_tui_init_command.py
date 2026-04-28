import pytest

from jig.tui.commands import get_handler, known_commands


def test_init_command_is_registered():
    assert "init" in known_commands()
    assert get_handler("init") is not None


@pytest.mark.asyncio
async def test_init_command_rejects_no_args():
    handler = get_handler("init")
    result = await handler(
        args=[],
        orch=None,
        project_path=None,
        prompt_registry=None,
        emitter=None,
    )
    assert result["ok"] is False
    assert "name" in result["error"]
