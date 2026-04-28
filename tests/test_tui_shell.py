"""Smoke tests for the TUI shell.

These don't run the TUI in a real terminal (Textual provides a
``Pilot`` for headless testing). They verify the app composes
correctly, screens are mounted, and tab navigation works.
"""
from pathlib import Path

import pytest

from jig.tui.app import JigApp


@pytest.mark.asyncio
async def test_app_composes_with_four_screens(tmp_path: Path):
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:  # noqa: F841
        for screen_id in ("now-pane", "tickets-pane", "spec-pane", "events-pane"):
            assert app.query_one(f"#{screen_id}") is not None


@pytest.mark.asyncio
async def test_app_switches_screens_via_hotkey(tmp_path: Path):
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("2")
        tabs = app.query_one("TabbedContent")
        assert tabs.active == "tickets-pane"
        await pilot.press("1")
        assert tabs.active == "now-pane"


@pytest.mark.asyncio
async def test_slash_help_writes_to_scrollback(tmp_path: Path):
    from textual.widgets import RichLog

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        # The Now tab is the default; type /help and press Enter.
        await pilot.press("slash", "h", "e", "l", "p", "enter")
        scrollback = app.query_one("#scrollback", RichLog)
        # RichLog stores rendered Strip objects in `.lines`; flatten to text.
        text = "\n".join(str(line) for line in scrollback.lines)
        assert "Available" in text or "help" in text.lower()


@pytest.mark.asyncio
async def test_help_overlay_opens_and_closes(tmp_path: Path):
    from jig.tui.screens.help import HelpScreen

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        assert not isinstance(app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_now_routes_input_to_prompt_reply_in_answering_mode(tmp_path: Path):
    """When a prompt_request arrives, the next Input.submit goes back as
    a prompt_reply command (not through the slash parser)."""
    from jig.tui.screens.now import NowScreen

    app = JigApp(project_path=tmp_path)
    sent_commands: list[tuple[str, dict]] = []

    async with app.run_test() as pilot:
        # Replace the daemon client's send_command with a recorder
        async def fake_send_command(name: str, args: dict) -> None:
            sent_commands.append((name, args))

        app.client.send_command = fake_send_command  # type: ignore[method-assign]

        now = app.query_one(NowScreen)

        # Simulate a prompt_request arriving from the daemon
        await now.handle_daemon_event({
            "type": "event",
            "topic": "prompts",
            "kind": "request",
            "data": {
                "prompt_id": "abc-123",
                "prompt_type": "brief_approval",
                "rendered": "(brief preview here)",
                "question": "Approve brief?",
                "options": [
                    {"key": "Y", "label": "Yes", "default": True},
                    {"key": "n", "label": "No"},
                ],
            },
        })

        # Verify we're in answering mode (placeholder updated)
        input_widget = app.query_one("#input")
        assert "Y/n" in input_widget.placeholder or "answer" in input_widget.placeholder

        # Submit "Y" directly via fake event to avoid key-naming brittleness
        class FakeEvent:
            def __init__(self, value: str) -> None:
                self.value = value
                self.input = type("X", (), {"clear": lambda self_: None})()

        await now.on_input_submitted(FakeEvent("Y"))  # type: ignore[arg-type]
        await pilot.pause(0.1)

        assert any(
            name == "prompt_reply" and args["args"][0] == "abc-123"
            for name, args in sent_commands
        ), f"expected prompt_reply with abc-123; got {sent_commands}"

        # After answering, prompt id should be cleared
        assert now._active_prompt_id is None


@pytest.mark.asyncio
async def test_now_renders_agent_render_events_to_scrollback(tmp_path: Path):
    from textual.widgets import RichLog

    from jig.tui.screens.now import NowScreen

    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        now = app.query_one(NowScreen)
        await now.handle_daemon_event({
            "type": "event",
            "topic": "agents",
            "kind": "render",
            "data": {"content": "TEST_RENDERED_LINE"},
        })
        scrollback = app.query_one("#scrollback", RichLog)
        text = "\n".join(str(line) for line in scrollback.lines)
        assert "TEST_RENDERED_LINE" in text
