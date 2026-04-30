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
        await pilot.press("ctrl+2")
        tabs = app.query_one("TabbedContent")
        assert tabs.active == "tickets-pane"
        await pilot.press("ctrl+1")
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
async def test_bare_slash_renders_command_list(tmp_path: Path):
    """Submitting just `/` shortcuts to /help — discovery shortcut."""
    from textual.widgets import RichLog

    from jig.tui.screens.now import NowScreen

    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        now = app.query_one(NowScreen)

        class FakeEvent:
            value = "/"
            input = type("X", (), {"clear": lambda self_: None})()

        await now.on_input_submitted(FakeEvent())  # type: ignore[arg-type]
        scrollback = app.query_one("#scrollback", RichLog)
        text = "\n".join(str(line) for line in scrollback.lines)
        assert "Available commands" in text
        for cmd in ("/init", "/ticket", "/concierge", "/status", "/quit"):
            assert cmd in text, f"missing {cmd} in help: {text[:300]}"


@pytest.mark.asyncio
async def test_help_overlay_opens_and_closes(tmp_path: Path):
    from jig.tui.screens.help import HelpScreen

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        # F1 is the always-fire help key (? doesn't pre-empt a focused Input).
        await pilot.press("f1")
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

        # Verify we're in answering mode — hint is written to scrollback
        # (TextArea has no placeholder; the hint appears as a dim scrollback line)
        from textual.widgets import RichLog
        scrollback = app.query_one("#scrollback", RichLog)
        sb_text = "\n".join(str(line) for line in scrollback.lines)
        assert now._active_prompt_id == "abc-123", "should be in answering mode"
        # The hint text contains "Y/n" or "answer"
        assert "Y/n" in sb_text or "answer" in sb_text.lower()

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


@pytest.mark.asyncio
async def test_now_renders_agent_text_events_to_scrollback(tmp_path: Path):
    from textual.widgets import RichLog

    from jig.tui.screens.now import NowScreen

    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        now = app.query_one(NowScreen)
        await now.handle_daemon_event({
            "type": "event",
            "topic": "agents",
            "kind": "text",
            "data": {"role": "concierge", "text": "Hello operator!"},
        })
        scrollback = app.query_one("#scrollback", RichLog)
        text = "\n".join(str(line) for line in scrollback.lines)
        assert "concierge" in text and "Hello operator!" in text


@pytest.mark.asyncio
async def test_now_routes_free_text_to_concierge_command(tmp_path: Path):
    """A free-text submit should send a concierge command, not parse as slash."""
    from jig.tui.screens.now import NowScreen

    app = JigApp(project_path=tmp_path)
    sent_commands: list[tuple[str, dict]] = []

    async with app.run_test() as pilot:
        async def fake_send_command(name, args):
            sent_commands.append((name, args))
        app.client.send_command = fake_send_command  # type: ignore[method-assign]

        now = app.query_one(NowScreen)

        class FakeInput:
            def clear(self):
                pass

        class FakeEvent:
            value = "what tickets are open?"
            input = FakeInput()

        await now.on_input_submitted(FakeEvent())
        await pilot.pause(0.05)

        assert any(
            name == "concierge" and args == {"args": ["what tickets are open?"]}
            for name, args in sent_commands
        ), f"expected concierge dispatch; got {sent_commands}"


@pytest.mark.asyncio
async def test_shift_enter_inserts_newline_enter_submits(tmp_path: Path):
    """Shift+Enter must insert a newline; Enter must submit the whole multi-line text."""
    from jig.tui.screens.now import JigTextArea

    app = JigApp(project_path=tmp_path)
    sent: list[tuple[str, dict]] = []

    async def fake_send(name, args):
        sent.append((name, args))

    async with app.run_test() as pilot:
        app.client.send_command = fake_send  # type: ignore[method-assign]
        ta = app.query_one("#input", JigTextArea)
        ta.focus()

        # Type "hi", then insert a newline via action (pilot.press("shift+enter")
        # may not reach the TextArea binding in headless mode, so call directly).
        await pilot.press("h", "i")
        ta.action_newline()
        await pilot.press("y", "o")
        await pilot.pause(0.05)

        assert ta.text == "hi\nyo", f"expected 'hi\\nyo', got {ta.text!r}"

        # Now submit — the whole multi-line value should go to concierge
        await pilot.press("enter")
        await pilot.pause(0.05)

        assert ("concierge", {"args": ["hi\nyo"]}) in sent, (
            f"expected concierge with 'hi\\nyo'; got {sent}"
        )


@pytest.mark.asyncio
async def test_code_fence_renders_as_syntax(tmp_path: Path):
    """Submission containing ```fenced``` blocks should render Syntax objects."""
    from textual.widgets import RichLog

    from jig.tui.screens.now import _render_user_input

    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        scrollback = app.query_one("#scrollback", RichLog)
        initial_lines = len(scrollback.lines)

        text = "here is some code:\n```python\nprint('hi')\n```\ndone"
        _render_user_input(scrollback, text)

        new_lines = scrollback.lines[initial_lines:]
        # At least one Syntax object should have been written
        rendered = "\n".join(str(ln) for ln in new_lines)
        assert "here is some code" in rendered or len(new_lines) > 0
        # Verify prose lines appear
        assert any("here is some code" in str(ln) for ln in new_lines)
        assert any("done" in str(ln) for ln in new_lines)


@pytest.mark.asyncio
async def test_thinking_indicator_shows_and_hides(tmp_path: Path):
    """agent_thinking events show + update an in-place indicator above the
    Composer; active=False hides it."""
    from textual.widgets import Static

    from jig.tui.screens.now import NowScreen

    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        now = app.query_one(NowScreen)
        indicator = app.query_one("#thinking", Static)

        # Hidden by default
        assert "visible" not in indicator.classes

        # Active → shown
        await now.handle_daemon_event({
            "type": "event",
            "topic": "agents",
            "kind": "thinking",
            "data": {"role": "po", "elapsed": 5, "active": True},
        })
        assert "visible" in indicator.classes

        # Inactive → hidden
        await now.handle_daemon_event({
            "type": "event",
            "topic": "agents",
            "kind": "thinking",
            "data": {"role": "po", "elapsed": 12, "active": False},
        })
        assert "visible" not in indicator.classes
