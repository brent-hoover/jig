"""MCP tool handlers for the init workflow.

Handlers are structured like ``ticket_mcp.py`` — pure-async functions
that take explicit dependencies, do I/O against the stores and the
filesystem, and return plain values. The ``mcp_server`` module wraps
them in ``@tool`` decorators with role-scoped visibility.
"""
from __future__ import annotations

from pathlib import Path

from jig.markdown_sections import get_section, list_sections, set_section
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff


def _brief_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "project.md"


async def handle_brief_list_sections(*, project_path: Path) -> list[str]:
    return list_sections(_brief_path(project_path))


async def handle_brief_get_section(*, project_path: Path, name: str) -> str:
    return get_section(_brief_path(project_path), name)


async def handle_brief_set_section(
    *,
    project_path: Path,
    name: str,
    markdown: str,
) -> None:
    set_section(_brief_path(project_path), name, markdown)


async def handle_po_finish_brief(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    summary: str,
    author: str,
) -> str:
    """Emit a Handoff on the brief ticket, targeting spec-generator."""
    path = _brief_path(project_path)
    if not path.is_file() or not list_sections(path):
        raise ValueError(
            "cannot finish an empty brief — add at least one section"
        )
    brief = await tickets.get("brief")
    if brief is None:
        raise KeyError("brief ticket not found")
    handoff = Handoff(
        ticket_id="brief",
        author=author,
        phase="spec-generator",
        outputs=[".jig/spec/project.md"],
        summary=summary,
    )
    entry_id = await threads.post(handoff)
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "handoff_posted",
                "ticket_id": "brief",
                "phase": "spec-generator",
            },
            topic="orchestrator",
        )
    )
    return entry_id
