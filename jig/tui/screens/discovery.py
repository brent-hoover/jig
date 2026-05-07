"""Discovery screen — L1 PO state + captured personas / journeys / playbacks.

Per ``docs/v2.0/multi-level-spec/design.md`` §"L1 — Discovery": the operator
can see the current persona / journey / phase, the captured capability
roster, and the per-journey playbacks at a glance. The screen is
read-only; mutations go through the L1 PO MCP tools (driven from the
TUI's slash commands or a real LLM-driven walk).

Renders:

- Header: project name + current persona/journey/phase.
- Personas list with their journey counts.
- Journeys list with capability counts + playback path.
- Capability roster.
- Pending capabilities (in-flight Phase-3 stash).

Data is loaded synchronously from disk via ``jig.spec_loader`` helpers
on each ``refresh()`` call. The screen exposes a ``refresh_now()``
method tests can call directly.
"""
from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.reactive import reactive
from textual.widgets import Static


class DiscoveryScreen(Container):
    """L1 discovery state + structured-cache projection."""

    DEFAULT_CSS = """
    DiscoveryScreen {
        layout: vertical;
        height: 1fr;
        padding: 1 2;
    }
    """

    project_path: reactive[Path | None] = reactive(None, recompose=False)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="discovery-header")
            yield Static(id="discovery-personas")
            yield Static(id="discovery-journeys")
            yield Static(id="discovery-roster")
            yield Static(id="discovery-pending")

    def on_mount(self) -> None:
        self.refresh_now()

    def set_project_path(self, project_path: Path | None) -> None:
        """Re-target the screen and refresh."""
        self.project_path = project_path
        self.refresh_now()

    def refresh_now(self) -> None:
        """Re-read state + cache from disk and re-render."""
        if self.project_path is None:
            self._render_empty("no project_path set")
            return
        try:
            self._render_state(self.project_path)
        except Exception as exc:  # noqa: BLE001
            self._render_empty(f"error: {exc}")

    # --- rendering -----------------------------------------------------------

    def _render_empty(self, msg: str) -> None:
        try:
            self.query_one("#discovery-header", Static).update(
                f"[bold]Discovery[/bold] [dim]({msg})[/dim]"
            )
        except Exception:
            return
        for sel in (
            "#discovery-personas",
            "#discovery-journeys",
            "#discovery-roster",
            "#discovery-pending",
        ):
            try:
                self.query_one(sel, Static).update("")
            except Exception:
                pass

    def _render_state(self, project_path: Path) -> None:
        from jig.po_l1_mcp import _read_staged, _staged_journeys_path
        from jig.spec_loader import (
            discovery_playback_path,
            load_discovery,
            load_discovery_state,
        )

        # Header — current phase / persona / journey from in-flight state.
        try:
            state = load_discovery_state(project_path)
        except FileNotFoundError:
            state = None
        header = "[bold]Discovery[/bold]"
        if state is None:
            header += " [dim](no state on disk yet)[/dim]"
        else:
            header += (
                f" status=[bold]{state.status}[/bold]"
                f"  current="
            )
            if state.current is None:
                header += "[dim]none[/dim]"
            else:
                header += (
                    f"persona={state.current.persona_id or '-'} "
                    f"journey={state.current.journey_id or '-'} "
                    f"phase={state.current.phase}/{state.current.step}"
                )
        self.query_one("#discovery-header", Static).update(header)

        # Body — committed doc (post-finalize) drives the personas /
        # journeys / roster lists. Pre-finalize, fall back to the staged
        # sidecars so the operator can still see what they've captured.
        try:
            doc = load_discovery(project_path)
        except FileNotFoundError:
            doc = None

        personas_lines: list[str] = ["[bold]Personas[/bold]"]
        journeys_lines: list[str] = ["[bold]Journeys[/bold]"]
        roster_lines: list[str] = ["[bold]Capability roster[/bold]"]
        if doc is not None:
            for p in doc.personas:
                count = sum(1 for j in doc.journeys if j.persona_id == p.id)
                personas_lines.append(f"- {{#{p.id}}} {p.description} [dim]({count} journeys)[/dim]")
            for j in doc.journeys:
                playback_rel = str(
                    discovery_playback_path(project_path, j.id).relative_to(project_path)
                )
                journeys_lines.append(
                    f"- [bold]{j.title}[/bold] {{#{j.id}}} "
                    f"[dim]persona={j.persona_id}, caps={len(j.capability_ids)}, "
                    f"playback={playback_rel}[/dim]"
                )
            for c in doc.capability_roster:
                roster_lines.append(
                    f"- {{#{c.id}}} {c.description} "
                    f"[dim]({len(c.journey_ids)} journeys)[/dim]"
                )
        else:
            # Staged path — pre-finalize.
            staged = _read_staged(_staged_journeys_path(project_path))
            personas_lines.append("[dim](no committed doc; pre-finalize)[/dim]")
            for entry in staged:
                journeys_lines.append(
                    f"- [bold]{entry.get('title', '?')}[/bold] "
                    f"{{#{entry.get('id', '?')}}} "
                    f"[dim](staged, persona={entry.get('persona_id', '-')})[/dim]"
                )
            roster_lines.append("[dim](roster only renders post-finalize)[/dim]")

        self.query_one("#discovery-personas", Static).update("\n".join(personas_lines))
        self.query_one("#discovery-journeys", Static).update("\n".join(journeys_lines))
        self.query_one("#discovery-roster", Static).update("\n".join(roster_lines))

        # Pending capabilities (mid-walk stash) — shown only when the
        # state file's pending list isn't empty so the screen stays
        # uncluttered for the post-finalize case.
        pending_lines: list[str] = []
        if state is not None and state.pending_capabilities:
            pending_lines.append("[bold]Pending (in-flight stash)[/bold]")
            for pc in state.pending_capabilities:
                pending_lines.append(
                    f"- {{#{pc.id}}} {pc.description} "
                    f"[dim](journey={pc.journey_id})[/dim]"
                )
        if state is not None and state.partial_walk:
            pending_lines.append("[bold]Partial walk[/bold]")
            for pw in state.partial_walk:
                marker = "[green]✓[/green]" if pw.confirmed else "[yellow]?[/yellow]"
                pending_lines.append(
                    f"- {marker} {{#{pw.id}}} {pw.description} "
                    f"[dim](journey={pw.journey_id})[/dim]"
                )
        self.query_one("#discovery-pending", Static).update(
            "\n".join(pending_lines) or ""
        )
