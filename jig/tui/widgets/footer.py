"""Persistent footer for the jig TUI.

Shows daemon connection state on the right, project context (cwd
basename + git branch + dirty marker) on the left. Refreshes on a
timer so git state stays current without operator interaction.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static

from jig.tui.daemon_client import ConnectionState


_DAEMON_COLORS = {
    ConnectionState.CONNECTED: "green",
    ConnectionState.CONNECTING: "yellow",
    ConnectionState.RECONNECTING: "yellow",
    ConnectionState.DISCONNECTED: "red",
}


def _git_status(path: Path) -> tuple[str | None, bool]:
    """Return (branch, dirty) for ``path`` or (None, False) if not a git repo."""
    try:
        # branch: short ref name; '--no-optional-locks' avoids index churn
        branch_result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if branch_result.returncode != 0:
            return None, False
        branch = branch_result.stdout.strip()
        # dirty: any uncommitted changes (index or working tree)
        status_result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=2,
        )
        dirty = bool(status_result.stdout.strip()) if status_result.returncode == 0 else False
        return branch, dirty
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None, False


class JigFooter(Widget):
    """Persistent footer: project context (left) + daemon state (right)."""

    DEFAULT_CSS = """
    JigFooter {
        height: 1;
        dock: bottom;
        background: $panel;
    }
    JigFooter > Horizontal {
        height: 1;
    }
    JigFooter #footer-left {
        width: 1fr;
        padding: 0 1;
    }
    JigFooter #footer-keys {
        width: auto;
        padding: 0 2;
        text-align: center;
    }
    JigFooter #footer-right {
        width: auto;
        padding: 0 1;
        text-align: right;
    }
    """

    _KEYS_TEXT = (
        "[dim]? help  1-4 tabs  b board  n new  ctrl+s sidebar  q quit[/dim]"
    )

    def __init__(self, *, project_path: Path | None = None) -> None:
        super().__init__()
        self._project_path = project_path
        self._project_text = Static("", id="footer-left", markup=True)
        self._keys_text = Static(self._KEYS_TEXT, id="footer-keys", markup=True)
        self._daemon_text = Static("daemon: ?", id="footer-right", markup=True)
        self._project_state: tuple[str, str | None, bool] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield self._project_text
            yield self._keys_text
            yield self._daemon_text

    def on_mount(self) -> None:
        self.refresh_project_state()
        # Re-poll git state every 5s so commits/edits are reflected.
        self.set_interval(5.0, self.refresh_project_state)

    def refresh_project_state(self) -> None:
        """Re-read cwd basename + git status from disk and re-render."""
        if self._project_path is None:
            self._project_text.update("")
            return
        path = self._project_path
        name = path.resolve().name or str(path)
        branch, dirty = _git_status(path)
        self._project_state = (name, branch, dirty)
        self._render_project()

    def _render_project(self) -> None:
        if self._project_state is None:
            self._project_text.update("")
            return
        name, branch, dirty = self._project_state
        parts = [f"[bold]{name}[/bold]"]
        if branch:
            marker = "[yellow]●[/yellow] " if dirty else ""
            parts.append(f"  [dim]on[/dim] {marker}[cyan]{branch}[/cyan]")
        self._project_text.update("".join(parts))

    def update_daemon_state(self, state: ConnectionState) -> None:
        color = _DAEMON_COLORS.get(state, "white")
        self._daemon_text.update(f"[{color}]daemon: {state.value}[/{color}]")
