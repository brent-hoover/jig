import pytest

from jig.tui.commands import get_handler, known_commands


def test_concierge_command_is_registered():
    assert "concierge" in known_commands()
    assert get_handler("concierge") is not None


@pytest.mark.asyncio
async def test_concierge_rejects_no_args():
    handler = get_handler("concierge")
    result = await handler(args=[], orch=None, project_path=None, emitter=None)
    assert result["ok"] is False
    assert (
        "query" in result["error"].lower() or "orchestrator" in result["error"].lower()
    )


@pytest.mark.asyncio
async def test_concierge_rejects_no_orch():
    handler = get_handler("concierge")
    result = await handler(
        args=["what is jig?"], orch=None, project_path=None, emitter=None
    )
    assert result["ok"] is False
    assert "orchestrator" in result["error"].lower()
