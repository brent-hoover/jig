"""MCP tool handlers for the init workflow.

Handlers are structured like ``ticket_mcp.py`` — pure-async functions
that take explicit dependencies, do I/O against the stores and the
filesystem, and return plain values. The ``mcp_server`` module wraps
them in ``@tool`` decorators with role-scoped visibility.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from jig.atomic import atomic_write_text
from jig.markdown_sections import get_section, list_sections, set_section
from jig.spec_generator import Gap
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.template_registry import list_templates
from jig.thread import Handoff, Note, SystemEvent


def _brief_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "project.md"


def _spec_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "project.structured.yaml"


def _arch_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "architecture.yaml"


def _yaml_get(data: Any, path: str) -> Any:
    """Walk dotted path; return None if any segment missing."""
    parts = path.split(".")
    current = data
    for p in parts:
        if isinstance(current, dict) and p in current:
            current = current[p]
        elif isinstance(current, list):
            try:
                idx = int(p)
            except ValueError:
                return None
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            return None
    return current


def _yaml_set(data: dict, path: str, value: Any) -> dict:
    """Walk dotted path, creating dicts (and extending lists) as needed."""
    parts = path.split(".")
    cursor: Any = data
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        try:
            idx: int | None = int(p)
        except ValueError:
            idx = None
        if last:
            if idx is not None and isinstance(cursor, list):
                while len(cursor) <= idx:
                    cursor.append(None)
                cursor[idx] = value
            else:
                cursor[p] = value
            return data
        # Non-terminal: ensure container exists.
        if idx is not None:
            if not isinstance(cursor, list):
                raise ValueError(
                    f"path {path!r}: segment {p} expects list, got {type(cursor).__name__}"
                )
            while len(cursor) <= idx:
                cursor.append({})
            if cursor[idx] is None:
                cursor[idx] = {}
            cursor = cursor[idx]
        else:
            if p not in cursor or not isinstance(cursor[p], (dict, list)):
                # Determine child type by peeking at next segment.
                next_p = parts[i + 1]
                try:
                    int(next_p)
                    cursor[p] = []
                except ValueError:
                    cursor[p] = {}
            cursor = cursor[p]
    return data


def _flatten_fields(data: Any, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            label = f"{prefix}.{k}" if prefix else str(k)
            out.append(label)
            out.extend(_flatten_fields(v, label))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            label = f"{prefix}.{i}" if prefix else str(i)
            out.append(label)
            out.extend(_flatten_fields(v, label))
    return out


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


async def handle_spec_get_field(*, project_path: Path, path: str) -> Any:
    spec_file = _spec_path(project_path)
    if not spec_file.is_file():
        return None
    data = yaml.safe_load(spec_file.read_text()) or {}
    return _yaml_get(data, path)


async def handle_spec_list_fields(*, project_path: Path) -> list[str]:
    spec_file = _spec_path(project_path)
    if not spec_file.is_file():
        return []
    data = yaml.safe_load(spec_file.read_text()) or {}
    return _flatten_fields(data)


async def handle_arch_get_field(*, project_path: Path, path: str) -> Any:
    arch_file = _arch_path(project_path)
    if not arch_file.is_file():
        return None
    data = yaml.safe_load(arch_file.read_text()) or {}
    return _yaml_get(data, path)


async def handle_arch_list_fields(*, project_path: Path) -> list[str]:
    arch_file = _arch_path(project_path)
    if not arch_file.is_file():
        return []
    data = yaml.safe_load(arch_file.read_text()) or {}
    return _flatten_fields(data)


async def handle_arch_set_field(
    *,
    threads: ThreadStore,
    project_path: Path,
    path: str,
    value: Any,
    author: str,
) -> None:
    arch_file = _arch_path(project_path)
    data = yaml.safe_load(arch_file.read_text()) if arch_file.is_file() else {}
    data = data or {}
    _yaml_set(data, path, value)
    atomic_write_text(arch_file, yaml.safe_dump(data, sort_keys=False))
    await threads.post(
        Note(
            ticket_id="architecture",
            author=author,
            text=f"arch_set_field {path}",
            payload={"path": path, "value": value},
        )
    )


async def handle_sa_propose_scaffold(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    template_name: str,
    rationale: str,
    config: dict,
    author: str,
) -> None:
    if not rationale.strip():
        raise ValueError("rationale must not be empty")
    if template_name not in list_templates():
        raise KeyError(f"unknown template: {template_name!r}")
    await threads.post(
        Note(
            ticket_id="architecture",
            author=author,
            text=f"Proposed scaffold: {template_name}\n\nRationale:\n{rationale}",
            payload={
                "kind": "sa_propose_scaffold",
                "template_name": template_name,
                "rationale": rationale,
                "config": config,
            },
        )
    )
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "sa_propose_scaffold",
                "ticket_id": "architecture",
                "template_name": template_name,
            },
            topic="orchestrator",
        )
    )
