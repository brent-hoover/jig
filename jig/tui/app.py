"""Top-level Textual app for jig."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import TabbedContent, TabPane

from jig.daemon import DaemonAlreadyRunning, daemon_paths, daemon_start, daemon_status
from jig.tui.daemon_client import ConnectionState, DaemonClient
from jig.tui.screens.agents import AgentsScreen
from jig.tui.screens.discovery import DiscoveryScreen
from jig.tui.screens.events import EventsScreen
from jig.tui.screens.now import NowScreen
from jig.tui.screens.ontology import OntologyScreen
from jig.tui.screens.spec import SpecScreen
from jig.tui.screens.suites import SuitesScreen
from jig.tui.screens.tickets import TicketsScreen
from jig.tui.widgets.footer import JigFooter
from jig.tui.widgets.sidebar import Sidebar


class JigApp(App):
    """The jig TUI. Single app, four tabbed screens."""

    CSS_PATH = "app.tcss"
    TITLE = "jig"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("question_mark", "help", "Help", priority=True),
        # F1 always-fires (terminal doesn't send it as a printable char,
        # so Input doesn't consume it).
        Binding("f1", "help", "Help", show=False, priority=True),
        # Bare digit keys: jump tabs when the focused widget doesn't consume
        # them (i.e. on Tickets/Spec/Events panes — not when typing in Now).
        # check_action() gates these to no-op when an Input has focus.
        Binding("1", "jump_unless_typing('now')", "Now", show=False),
        Binding("2", "jump_unless_typing('tickets')", "Tickets", show=False),
        Binding("3", "jump_unless_typing('spec')", "Spec", show=False),
        Binding("4", "jump_unless_typing('events')", "Events", show=False),
        # Modifier-prefixed always-fire variants, browser-style — work even
        # while the Input has focus.
        Binding("ctrl+1", "switch_screen('now')", "Now", show=False, priority=True),
        Binding(
            "ctrl+2", "switch_screen('tickets')", "Tickets", show=False, priority=True
        ),
        Binding("ctrl+3", "switch_screen('spec')", "Spec", show=False, priority=True),
        Binding(
            "ctrl+4", "switch_screen('events')", "Events", show=False, priority=True
        ),
        Binding(
            "ctrl+5", "switch_screen('agents')", "Agents", show=False, priority=True
        ),
        # Pane-local bindings routed here because ContentTabs holds focus
        Binding("b", "toggle_board", "List/Board", show=False),
        Binding("n", "new_ticket", "New", show=False),
        Binding("e", "edit_ticket", "Edit", show=False),
        Binding("r", "spec_raw_yaml", "Raw YAML", show=False),
        # Events pane hotkeys
        Binding("f", "cycle_event_filter", "Filter", show=False),
        Binding("F", "toggle_event_follow", "Follow", show=False),
        # priority=True so App wins over ListView.select_cursor when events pane
        # is active; check_action() returns False on other panes so the event
        # falls through to the focused widget unchanged.
        Binding("enter", "open_event_detail", "Detail", show=False, priority=True),
        # Paste clipboard image into the focused Composer (saves to
        # .jig/uploads/ and inserts a [image: PATH] reference).
        Binding("ctrl+i", "paste_image", "Paste image", show=False, priority=True),
        Binding("ctrl+s", "toggle_sidebar", "Sidebar", show=False, priority=True),
        # Shift+Tab on Now pane: toggle focus between Composer and Scrollback
        # so the operator can scroll the transcript with arrow keys.
        Binding(
            "shift+tab", "toggle_scroll_focus", "Scroll", show=False, priority=True
        ),
    ]

    daemon_state: reactive[ConnectionState] = reactive(ConnectionState.DISCONNECTED)

    def __init__(self, project_path: Path) -> None:
        super().__init__()
        self.project_path = project_path

        def _resolve_addr() -> str:
            addr_file = daemon_paths(self.project_path).socket_addr_file
            if not addr_file.is_file():
                raise FileNotFoundError(f"daemon not running: {addr_file} missing")
            return addr_file.read_text().strip()

        self.client = DaemonClient(addr_provider=_resolve_addr)

    def compose(self) -> ComposeResult:
        with Horizontal():
            with TabbedContent(initial="now-pane"):
                with TabPane("Now", id="now-pane"):
                    yield NowScreen()
                with TabPane("Tickets", id="tickets-pane"):
                    yield TicketsScreen()
                with TabPane("Agents", id="agents-pane"):
                    yield AgentsScreen()
                with TabPane("Spec", id="spec-pane"):
                    yield SpecScreen()
                with TabPane("Discovery", id="discovery-pane"):
                    yield DiscoveryScreen()
                with TabPane("Suites", id="suites-pane"):
                    yield SuitesScreen()
                with TabPane("Ontology", id="ontology-pane"):
                    yield OntologyScreen()
                with TabPane("Events", id="events-pane"):
                    yield EventsScreen()
            yield Sidebar()
        yield JigFooter(project_path=self.project_path)

    async def on_mount(self) -> None:
        # Track B Final — hand the project_path to the multi-level PO
        # screens so they can read their on-disk artifacts directly.
        # Each screen's set_project_path triggers a re-render.
        for screen_cls in (AgentsScreen, DiscoveryScreen, SuitesScreen, OntologyScreen):
            try:
                screen = self.query_one(screen_cls)
            except Exception:
                continue
            try:
                screen.set_project_path(self.project_path)
            except Exception:
                pass

        self._maybe_autostart_daemon()

        self.run_worker(
            self.client.run_with_reconnect(
                on_message=self._handle_daemon_message,
                on_state_change=self._on_daemon_state,
                topics=["tickets", "spec", "agents", "events", "prompts"],
            ),
            exclusive=True,
            name="daemon-client",
        )

    def _maybe_autostart_daemon(self) -> None:
        """Start the daemon automatically when the project is initialized but idle."""
        arch = self.project_path / "docs" / "architecture.yaml"
        if not arch.is_file():
            return  # project not initialized yet
        status = daemon_status(self.project_path)
        if status.running:
            return
        try:
            daemon_start(self.project_path, docker=False)
        except (DaemonAlreadyRunning, RuntimeError):
            pass

    async def _handle_daemon_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        topic = msg.get("topic")

        if msg_type == "result":
            try:
                now = self.query_one(NowScreen)
            except Exception:
                return
            await now.handle_command_result(msg)
            return

        if msg_type == "snapshot":
            if topic == "tickets":
                try:
                    tickets = self.query_one(TicketsScreen)
                except Exception:
                    pass
                else:
                    await tickets.handle_snapshot(msg.get("data"))
                self._sidebar_safe(lambda s: s.update_tickets_snapshot(msg.get("data")))
            if topic == "spec":
                try:
                    spec = self.query_one(SpecScreen)
                except Exception:
                    return
                await spec.handle_snapshot(msg.get("data"))
            if topic == "events":
                try:
                    ev_screen = self.query_one(EventsScreen)
                except Exception:
                    pass
                else:
                    await ev_screen.handle_snapshot(msg.get("data"))
                self._sidebar_safe(lambda s: s.update_events_snapshot(msg.get("data")))
            return

        if msg_type == "event":
            if topic in ("agents", "prompts", "events"):
                try:
                    now = self.query_one(NowScreen)
                except Exception:
                    pass
                else:
                    await now.handle_daemon_event(msg)
                # agent_thinking events drive Sidebar's Activity subzone
                if topic == "agents" and msg.get("kind") == "thinking":
                    self._sidebar_safe(
                        lambda s: s.update_thinking(msg.get("data") or {})
                    )
                # agent_tool events feed the per-agent tool history in Activity
                if topic == "agents" and msg.get("kind") == "tool":
                    self._sidebar_safe(
                        lambda s: s.update_tool_use(msg.get("data") or {})
                    )
                # prompt request/response drives Sidebar's Needs You subzone
                if topic == "prompts":
                    kind = msg.get("kind")
                    if kind == "request":
                        data = msg.get("data") or {}
                        self._sidebar_safe(lambda s: s.update_prompt(data))
                    elif kind == "response":
                        self._sidebar_safe(lambda s: s.update_prompt(None))
                # Route all agent events to the Agents screen
                self._agents_screen_safe(msg.get("kind", ""), msg.get("data") or {})
                # Lifecycle events (ticket_dispatched / ticket_completed /
                # ticket_failed / ticket_merge_conflict) feed the Recent
                # subzone so the operator sees what's happening in real
                # time, not just on snapshot refresh.
                if topic == "events":
                    kind = msg.get("kind", "")
                    payload = msg.get("data") or {}
                    self._sidebar_safe(
                        lambda s: s.append_event(
                            {"event_type": kind, "payload": payload}
                        )
                    )
                return
            if topic == "tickets":
                try:
                    tickets = self.query_one(TicketsScreen)
                except Exception:
                    pass
                else:
                    kind = msg.get("kind", "")
                    data = msg.get("data") or {}
                    await tickets.handle_event(kind, data)
                # Also feed Sidebar's Queue subzone
                self._sidebar_safe(
                    lambda s: s.update_ticket_event(
                        msg.get("kind", ""), msg.get("data") or {}
                    )
                )
                # Tail also picks up ticket-related events
                self._sidebar_safe(lambda s: s.append_event(msg.get("data") or {}))
                return

    def _sidebar_safe(self, fn) -> None:
        try:
            sidebar = self.query_one(Sidebar)
        except Exception:
            return
        fn(sidebar)

    def _agents_screen_safe(self, kind: str, data: dict) -> None:
        try:
            screen = self.query_one(AgentsScreen)
        except Exception:
            return
        dispatch = {
            "start": screen.handle_agent_start,
            "thinking": screen.handle_agent_thinking,
            "tool": screen.handle_agent_tool,
            "tool_result": screen.handle_agent_tool_result,
        }
        handler = dispatch.get(kind)
        if handler is not None:
            handler(data)

    def action_toggle_sidebar(self) -> None:
        self._sidebar_safe(lambda s: s.toggle_class("-hidden"))

    def action_paste_image(self) -> None:
        """Read an image from the system clipboard, save it under
        ``.jig/uploads/``, and insert ``[image: <abs path>]`` into the
        focused TextArea Composer.

        macOS via osascript; Linux via wl-paste / xclip; other platforms
        unsupported (graceful error notify).
        """
        from datetime import datetime

        from textual.widgets import TextArea

        from jig.tui.clipboard import (
            ClipboardImageError,
            get_clipboard_image_bytes,
        )

        focused = self.focused
        if not isinstance(focused, TextArea):
            self.notify(
                "Ctrl+I works in the Now Composer (focus it first)",
                severity="warning",
                timeout=3,
            )
            return
        try:
            data = get_clipboard_image_bytes()
        except ClipboardImageError as exc:
            self.notify(f"clipboard: {exc}", severity="warning", timeout=4)
            return

        # Save under .jig/uploads/jig-<UTC-timestamp>.png
        uploads = self.project_path / ".jig" / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        out_path = uploads / f"jig-clip-{ts}.png"
        out_path.write_bytes(data)

        # Insert "[image: ABS_PATH]" at the cursor position.
        ref = f"[image: {out_path.resolve()}]"
        focused.insert(ref)
        self.notify(
            f"Saved screenshot → {out_path.name} ({len(data) // 1024} KB)",
            severity="information",
            timeout=3,
        )

    def _on_daemon_state(self, state: ConnectionState) -> None:
        self.daemon_state = state

    def watch_daemon_state(self, new: ConnectionState) -> None:
        try:
            footer = self.query_one(JigFooter)
        except Exception:
            return  # mount may not have run yet
        footer.update_daemon_state(new)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Gate open_event_detail so its priority=True binding yields on other panes.

        Returning False causes run_action to return False, which makes _check_bindings
        continue rather than consuming the key event.  This lets widgets (e.g. Input,
        ListView) handle 'enter' normally when the events pane is not active.
        """
        if action == "open_event_detail":
            return True if self._events_pane_active() else False
        # Shift+Tab toggle only fires on the Now pane; elsewhere fall through
        # to Textual's default focus-previous traversal.
        if action == "toggle_scroll_focus":
            return True if self._now_pane_active() else False
        # Bare-digit jump bindings: yield when the focused widget would
        # consume the digit (Now's Input is focused and the operator is
        # typing). Returning False makes the key fall through to the
        # widget. The ctrl+digit variants use action_switch_screen and
        # are NOT gated — they always fire.
        if action == "jump_unless_typing":
            try:
                focused = self.focused
            except Exception:
                focused = None
            from textual.widgets import Input as _Input, TextArea as _TextArea

            if isinstance(focused, (_Input, _TextArea)):
                return False
        return super().check_action(action, parameters)

    def action_switch_screen(self, screen_id: str) -> None:
        tabs = self.query_one(TabbedContent)
        tabs.active = f"{screen_id}-pane"

    def action_jump_unless_typing(self, screen_id: str) -> None:
        """Pane jump that yields to a focused Input via check_action()."""
        self.action_switch_screen(screen_id)

    def action_help(self) -> None:
        from jig.tui.screens.help import HelpScreen

        self.push_screen(HelpScreen())

    def _tickets_pane_active(self) -> bool:
        try:
            tabs = self.query_one(TabbedContent)
        except Exception:
            return False
        return tabs.active == "tickets-pane"

    def _spec_pane_active(self) -> bool:
        try:
            tabs = self.query_one(TabbedContent)
        except Exception:
            return False
        return tabs.active == "spec-pane"

    def _now_pane_active(self) -> bool:
        try:
            tabs = self.query_one(TabbedContent)
        except Exception:
            return False
        return tabs.active == "now-pane"

    def action_toggle_scroll_focus(self) -> None:
        """Move focus between the Now Composer and the Scrollback.

        With the scrollbar gone, the operator scrolls the transcript by
        focusing the RichLog and using up/down/page-up/page-down.
        """
        from textual.widgets import RichLog

        from jig.tui.screens.now import JigTextArea

        try:
            now = self.query_one(NowScreen)
        except Exception:
            return
        try:
            scrollback = now.query_one("#scrollback", RichLog)
            composer = now.query_one("#input", JigTextArea)
        except Exception:
            return
        if scrollback.has_focus:
            composer.focus()
        else:
            scrollback.focus()

    def _events_pane_active(self) -> bool:
        try:
            tabs = self.query_one(TabbedContent)
        except Exception:
            return False
        return tabs.active == "events-pane"

    def action_toggle_board(self) -> None:
        """Toggle board view on the Tickets pane, or open brief on Spec pane."""
        if self._tickets_pane_active():
            try:
                tickets = self.query_one(TicketsScreen)
                tickets.action_toggle_view()
            except Exception:
                pass
            return
        if self._spec_pane_active():
            self.run_worker(self._show_brief_modal(), exclusive=False)

    async def action_spec_raw_yaml(self) -> None:
        if not self._spec_pane_active():
            return
        from jig.tui.screens.spec_modals import RawYamlModal

        spec = self.query_one(SpecScreen).spec
        await self.push_screen(RawYamlModal(spec=spec))

    async def _show_brief_modal(self) -> None:
        from jig.tui.screens.spec_modals import BriefModal

        await self.push_screen(BriefModal(project_path=self.project_path))

    async def action_new_ticket(self) -> None:
        """Push NewTicketModal when the Tickets pane is active."""
        if not self._tickets_pane_active():
            return
        from jig.tui.screens.ticket_form import NewTicketModal

        async def on_submit_async(data: dict) -> None:
            flagged: list[str] = []
            for k in ("title", "type", "size", "assignee", "description"):
                if k in data and data[k]:
                    flagged.extend([f"--{k}", data[k]])
            await self.client.send_command("ticket", {"args": ["new", *flagged]})

        def on_submit(data: dict) -> None:
            self.run_worker(on_submit_async(data), exclusive=False)

        await self.push_screen(NewTicketModal(on_submit=on_submit))

    async def action_edit_ticket(self) -> None:
        """Push EditTicketModal for the selected ticket when the Tickets pane is active."""
        if not self._tickets_pane_active():
            return
        from jig.tui.screens.ticket_form import EditTicketModal
        from textual.widgets import ListView

        try:
            screen = self.query_one(TicketsScreen)
        except Exception:
            return
        try:
            list_view = screen.query_one("#tickets-list", ListView)
        except Exception:
            return
        if list_view.index is None or list_view.index >= len(list_view.children):
            return
        item = list_view.children[list_view.index]
        ticket_id = getattr(item, "ticket_id", None)
        if not ticket_id or ticket_id not in screen.tickets:
            return
        ticket = screen.tickets[ticket_id]

        async def on_submit_async(tid: str, changes: dict) -> None:
            kvs = [f"{k}={v}" for k, v in changes.items()]
            if kvs:
                await self.client.send_command(
                    "ticket", {"args": ["update", tid, *kvs]}
                )

        def on_submit(tid: str, changes: dict) -> None:
            self.run_worker(on_submit_async(tid, changes), exclusive=False)

        await self.push_screen(EditTicketModal(ticket=ticket, on_submit=on_submit))

    async def action_cycle_event_filter(self) -> None:
        if not self._events_pane_active():
            return
        await self.query_one(EventsScreen).cycle_filter()

    async def action_toggle_event_follow(self) -> None:
        if not self._events_pane_active():
            return
        await self.query_one(EventsScreen).toggle_follow()

    async def action_open_event_detail(self) -> None:
        if not self._events_pane_active():
            return
        from jig.tui.screens.event_detail_modal import EventDetailModal

        selected = self.query_one(EventsScreen).get_selected_event()
        if selected is None:
            return
        await self.push_screen(EventDetailModal(event=selected))
