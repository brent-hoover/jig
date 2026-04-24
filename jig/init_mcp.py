"""MCP tool handlers for the init workflow.

Handlers are structured like ``ticket_mcp.py`` — pure-async functions
that take explicit dependencies, do I/O against the stores and the
filesystem, and return plain values. The ``mcp_server`` module wraps
them in ``@tool`` decorators with role-scoped visibility.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from jig.atomic import atomic_write_text
from jig.markdown_sections import get_section, list_sections, set_section
from jig.spec_generator import Gap
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.thread import Handoff, Note, SystemEvent


def _brief_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "project.md"


def _spec_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "project.structured.yaml"


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
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    summary: str,
    author: str,
) -> str:
    """Emit a Handoff on the brief ticket, targeting spec-generator."""
    path = _brief_path(project_path)
    if not list_sections(path):
        raise ValueError(
            "cannot finish an empty brief — add at least one section"
        )
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


async def handle_spec_publish(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    yaml_content: str,
    advisory_notes: list[str],
    author: str,
) -> None:
    """Write the structured spec and emit spec_generated."""
    try:
        yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise ValueError(f"cannot parse spec YAML: {e}") from e
    atomic_write_text(_spec_path(project_path), yaml_content)
    if advisory_notes:
        await threads.post(
            Note(
                ticket_id="brief",
                author=author,
                text="Advisory notes:\n" + "\n".join(f"- {n}" for n in advisory_notes),
                payload={"advisory_notes": list(advisory_notes)},
            )
        )
    await threads.post(
        SystemEvent(
            ticket_id="brief",
            author=author,
            event_type="spec_generated",
            content="structured spec written",
        )
    )
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "spec_generated", "ticket_id": "brief"},
            topic="orchestrator",
        )
    )


async def handle_spec_report_gaps(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    gaps: list[Gap],
    author: str,
) -> None:
    """Post a structured Note and emit spec_gaps_reported."""
    rendered = "\n".join(
        f"- [{g.severity}] {g.location}: {g.description}" for g in gaps
    )
    await threads.post(
        Note(
            ticket_id="brief",
            author=author,
            text=f"Gaps:\n{rendered}",
            payload={"gaps": [g.model_dump() for g in gaps]},
        )
    )
    await threads.post(
        SystemEvent(
            ticket_id="brief",
            author=author,
            event_type="spec_gaps_reported",
            content=f"{len(gaps)} gap(s) reported",
        )
    )
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "spec_gaps_reported", "ticket_id": "brief"},
            topic="orchestrator",
        )
    )
