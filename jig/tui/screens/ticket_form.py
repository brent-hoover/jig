"""Modal forms for creating / editing tickets.

Submitted forms call back into the parent (TicketsScreen) which
dispatches the appropriate /ticket slash command via the daemon
client. Modals don't talk to the daemon directly — keep the
side-effect surface narrow.
"""

from __future__ import annotations

from typing import Any, Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static


_WORK_TYPES = [
    ("feature", "feature"),
    ("bugfix", "bugfix"),
    ("refactor", "refactor"),
    ("spike", "spike"),
    ("perf", "perf"),
    ("migration", "migration"),
    ("docs", "docs"),
]
_SIZES = [(s, s) for s in ("xs", "s", "m", "l", "xl")]
_STATUSES = [
    ("open", "open"),
    ("in_progress", "in_progress"),
    ("blocked", "blocked"),
    ("needs_info", "needs_info"),
    ("failed", "failed"),
    ("merge_conflict", "merge_conflict"),
    ("resolved", "resolved"),
    ("closed", "closed"),
]


class NewTicketModal(ModalScreen):
    """Modal form for creating a new ticket."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    DEFAULT_CSS = """
    NewTicketModal {
        align: center middle;
    }
    NewTicketModal > Container {
        background: $panel;
        border: thick $accent;
        padding: 1 2;
        width: 70;
        height: auto;
    }
    NewTicketModal Label {
        margin-top: 1;
    }
    NewTicketModal #buttons {
        margin-top: 1;
        align-horizontal: right;
        height: 3;
    }
    NewTicketModal Button {
        margin-left: 1;
    }
    """

    def __init__(self, on_submit: Callable[[dict[str, str]], Any]) -> None:
        super().__init__()
        self._on_submit = on_submit

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("[bold]New Ticket[/bold]", markup=True)
            yield Label("Title")
            yield Input(id="f-title", placeholder="ticket title")
            yield Label("Type")
            yield Select(_WORK_TYPES, id="f-type", value="feature", allow_blank=False)
            yield Label("Size")
            yield Select(_SIZES, id="f-size", value="m", allow_blank=False)
            yield Label("Assignee (optional)")
            yield Input(id="f-assignee", placeholder="role or agent id")
            yield Label("Description (optional)")
            yield Input(id="f-description", placeholder="one-line description")
            with Container(id="buttons"):
                yield Button("Cancel", id="btn-cancel")
                yield Button("Create", id="btn-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#f-title", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
            return
        if event.button.id == "btn-submit":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Allow Enter on the last input to submit
        if event.input.id == "f-description":
            self._submit()

    def _submit(self) -> None:
        title = self.query_one("#f-title", Input).value.strip()
        if not title:
            # Focus title and return — empty title is invalid
            self.query_one("#f-title", Input).focus()
            return
        data: dict[str, str] = {
            "title": title,
            "type": self.query_one("#f-type", Select).value,
            "size": self.query_one("#f-size", Select).value,
        }
        assignee = self.query_one("#f-assignee", Input).value.strip()
        description = self.query_one("#f-description", Input).value.strip()
        if assignee:
            data["assignee"] = assignee
        if description:
            data["description"] = description
        self._on_submit(data)
        self.dismiss(data)


class EditTicketModal(ModalScreen):
    """Modal form for editing an existing ticket's status / assignee."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
    ]

    DEFAULT_CSS = """
    EditTicketModal {
        align: center middle;
    }
    EditTicketModal > Container {
        background: $panel;
        border: thick $accent;
        padding: 1 2;
        width: 70;
        height: auto;
    }
    EditTicketModal Label {
        margin-top: 1;
    }
    EditTicketModal #buttons {
        margin-top: 1;
        align-horizontal: right;
        height: 3;
    }
    EditTicketModal Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        ticket: dict[str, Any],
        on_submit: Callable[[str, dict[str, str]], Any],
    ) -> None:
        super().__init__()
        self._ticket = ticket
        self._on_submit = on_submit

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(
                f"[bold]Edit Ticket:[/bold] {self._ticket.get('title', '?')}",
                markup=True,
            )
            yield Label("Status")
            yield Select(
                _STATUSES,
                id="f-status",
                value=self._ticket.get("status", "open"),
                allow_blank=False,
            )
            yield Label("Assignee")
            yield Input(
                id="f-assignee",
                value=self._ticket.get("assignee") or "",
                placeholder="role or agent id (blank to clear)",
            )
            with Container(id="buttons"):
                yield Button("Cancel", id="btn-cancel")
                yield Button("Save", id="btn-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#f-status", Select).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
            return
        if event.button.id == "btn-submit":
            self._submit()

    def _submit(self) -> None:
        new_status = self.query_one("#f-status", Select).value
        new_assignee = self.query_one("#f-assignee", Input).value.strip()
        changes: dict[str, str] = {}
        if new_status != self._ticket.get("status"):
            changes["status"] = new_status
        old_assignee = self._ticket.get("assignee") or ""
        if new_assignee != old_assignee:
            changes["assignee"] = new_assignee
        if changes:
            self._on_submit(self._ticket["id"], changes)
        self.dismiss(changes)
