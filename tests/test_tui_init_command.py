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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        ["demo", "--auto", "--profile", "medium"],
        ["demo", "--auto", "--profile=medium"],
    ],
)
async def test_init_command_parses_and_forwards_profile(monkeypatch, tmp_path, args):
    """The TUI /init must parse --profile NAME / --profile=NAME and forward it
    to run_init (so e.g. /init --brief --profile small works under the new
    --brief-requires-profile rule)."""
    from unittest.mock import MagicMock

    import jig.init_workflow as iw
    import jig.tui.console_stream as cs

    captured: dict = {}

    async def fake_run_init(**kw):
        captured.update(kw)

    monkeypatch.setattr(iw, "run_init", fake_run_init)
    monkeypatch.setattr(cs, "make_streaming_console", lambda emitter: MagicMock())

    handler = get_handler("init")
    result = await handler(
        args=args,
        orch=None,
        project_path=tmp_path,
        prompt_registry=None,
        emitter=MagicMock(),
    )
    assert result["ok"] is True
    assert captured["profile_name"] == "medium"
