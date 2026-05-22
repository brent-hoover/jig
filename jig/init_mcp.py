"""MCP tool handlers for the init workflow.

Handlers are structured like ``ticket_mcp.py`` — pure-async functions
that take explicit dependencies, do I/O against the stores and the
filesystem, and return plain values. The ``mcp_server`` module wraps
them in ``@tool`` decorators with role-scoped visibility.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from jig.atomic import atomic_write_text
from jig.brief_parser import BriefParseError, parse_brief
from jig.handoff_resolve import resolve_after_handoff as _resolve_after_handoff
from jig.markdown_sections import get_section, list_sections, set_section
from jig.spec_regeneration import regenerate
from jig.spec_schema import StructuredSpec
from jig.uri import ProjectUriError, resolve_spec_uri
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.template_registry import list_templates, load_template_metadata
from jig.thread import Handoff, Note, SystemEvent

if TYPE_CHECKING:
    from jig.spec_generator import Gap


def _brief_path(project_path: Path) -> Path:
    return project_path / "docs" / "brief.md"


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
            if not isinstance(cursor, dict):
                raise ValueError(
                    f"path {path!r}: segment {p!r} cannot descend into "
                    f"{type(cursor).__name__}"
                )
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
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    summary: str,
    author: str,
) -> str:
    """Emit a Handoff on the brief ticket, targeting spec-generator,
    and mark the brief RESOLVED so the PO agent exits cleanly."""
    path = _brief_path(project_path)
    if not list_sections(path):
        raise ValueError("cannot finish an empty brief — add at least one section")
    handoff = Handoff(
        ticket_id="brief",
        author=author,
        phase="spec-generator",
        outputs=["docs/brief.md"],
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
    await _resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id="brief",
        author=author,
    )
    return entry_id


async def handle_spec_publish(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    yaml_content: str,
    advisory_notes: list[str],
    author: str,
) -> None:
    """Write the structured spec, emit spec_generated, and resolve
    the brief ticket so the spec-generator exits cleanly."""
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise ValueError(f"cannot parse spec YAML: {e}") from e
    try:
        StructuredSpec.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"spec does not match schema: {e}") from e
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
    await _resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id="brief",
        author=author,
    )


async def handle_spec_report_gaps(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    gaps: list[Gap],
    author: str,
) -> None:
    """Post a structured Note, emit spec_gaps_reported, and resolve
    the brief ticket so the spec-generator exits cleanly. The init
    workflow will route to GAP_PROMPT next, prompting the operator
    to either resume PO (which reactivates the brief) or quit."""
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
    await _resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id="brief",
        author=author,
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


async def handle_arch_list_templates() -> list[dict[str, Any]]:
    """Enumerate available scaffold templates with metadata.

    SA calls this before ``sa_propose_scaffold`` to learn what's
    actually installed instead of guessing template names. Each entry
    is a dict with ``name``, ``description``, ``language``,
    ``framework``, and ``deploy_target``.
    """
    out: list[dict[str, Any]] = []
    for name in list_templates():
        md = load_template_metadata(name)
        out.append(
            {
                "name": md.name,
                "description": md.description,
                "language": md.language,
                "framework": md.framework,
                "deploy_target": md.deploy_target,
            }
        )
    return out


async def handle_sa_propose_scaffold(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    template_name: str,
    rationale: str,
    decisions: dict | None = None,
    constraints: list[str] | None = None,
    open_questions: list[dict] | None = None,
    config: dict | None = None,  # deprecated; use decisions
    author: str,
) -> None:
    if not rationale.strip():
        raise ValueError("rationale must not be empty")
    available = list_templates()
    if template_name not in available:
        raise KeyError(
            f"unknown template: {template_name!r}. "
            f"Available templates: {available}. "
            "Call `arch_list_templates` for full metadata."
        )
    # Merge legacy config into decisions for backwards compat
    merged_decisions = dict(config or {})
    merged_decisions.update(decisions or {})
    await threads.post(
        Note(
            ticket_id="architecture",
            author=author,
            text=f"Proposed scaffold: {template_name}\n\nRationale:\n{rationale}",
            payload={
                "kind": "sa_propose_scaffold",
                "template_name": template_name,
                "rationale": rationale,
                "decisions": merged_decisions,
                "constraints": constraints or [],
                "open_questions": open_questions or [],
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
    await _resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id="architecture",
        author=author,
    )


def _load_spec(project_path: Path) -> StructuredSpec | None:
    """Load and validate the structured spec; None if absent."""
    spec_file = _spec_path(project_path)
    if not spec_file.is_file():
        return None
    data = yaml.safe_load(spec_file.read_text()) or {}
    return StructuredSpec.model_validate(data)


async def handle_spec_load_existing(*, project_path: Path) -> dict[str, Any]:
    """Return the existing structured spec as a dict, or {} if absent.

    Spec-gen calls this at the top of every regen run.
    """
    spec = _load_spec(project_path)
    if spec is None:
        return {}
    return spec.model_dump(mode="json", by_alias=True)


async def handle_spec_list_capabilities(
    *,
    project_path: Path,
    state: str | None = None,
) -> list[dict[str, Any]]:
    spec = _load_spec(project_path)
    if spec is None:
        return []
    out = [
        {"id": c.id, "title": c.title, "state": c.state.value}
        for c in spec.capabilities
        if state is None or c.state.value == state
    ]
    return out


async def handle_spec_get_capability(
    *,
    project_path: Path,
    id: str,
) -> dict[str, Any]:
    spec = _load_spec(project_path)
    if spec is None:
        raise KeyError(f"no spec yet; cannot get capability {id!r}")
    cap = spec.capability_by_id_or_alias(id)
    if cap is None:
        raise KeyError(f"capability {id!r} not found")
    return cap.model_dump(mode="json", by_alias=True)


async def handle_spec_get_behavior(
    *,
    project_path: Path,
    capability_id: str,
    behavior_id: str,
) -> dict[str, Any]:
    cap_data = await handle_spec_get_capability(
        project_path=project_path,
        id=capability_id,
    )
    for b in cap_data.get("behaviors", []):
        if b["id"] == behavior_id:
            return b
    raise KeyError(
        f"behavior {behavior_id!r} not found in capability {capability_id!r}"
    )


async def handle_spec_list_non_goals(
    *,
    project_path: Path,
) -> list[dict[str, Any]]:
    spec = _load_spec(project_path)
    if spec is None:
        return []
    return [
        {"id": n.id, "text": n.text, "rationale": n.rationale} for n in spec.non_goals
    ]


async def handle_spec_get_non_goal(
    *,
    project_path: Path,
    id: str,
) -> dict[str, Any]:
    spec = _load_spec(project_path)
    if spec is None:
        raise KeyError(f"no spec yet; cannot get non-goal {id!r}")
    ng = spec.non_goal_by_id_or_alias(id)
    if ng is None:
        raise KeyError(f"non-goal {id!r} not found")
    return ng.model_dump(mode="json")


async def handle_spec_resolve_uri(
    *,
    project_path: Path,
    uri: str,
) -> dict[str, Any]:
    spec = _load_spec(project_path)
    if spec is None:
        raise KeyError(f"no spec yet; cannot resolve {uri!r}")
    try:
        return resolve_spec_uri(uri, spec)
    except ProjectUriError as e:
        raise KeyError(str(e)) from e


async def handle_spec_generate_from_brief(
    *,
    project_path: Path,
    tickets: TicketStore,
) -> dict[str, Any]:
    """Run the full spec-generation pipeline against the current brief
    and existing structured spec.

    Returns ``{"spec": dict | None, "gaps": [...]}``:
      - On format violations or removed-from-brief: ``spec=None`` and
        a list of gap dicts with ``severity="blocking"``.
      - On success: ``spec`` is the merged StructuredSpec as a dict
        (ready to pass to ``spec_publish``); ``gaps`` may contain
        advisory entries for the operator.

    Spec-gen's MCP-callable orchestration entry point — collapses what
    used to be parse + load_existing + merge + validate into one call.
    """
    brief_path = _brief_path(project_path)
    if not brief_path.is_file():
        return {
            "spec": None,
            "gaps": [
                {
                    "kind": "missing",
                    "location": str(brief_path),
                    "description": "no brief.md found",
                    "severity": "blocking",
                }
            ],
        }

    try:
        parsed_brief = parse_brief(brief_path.read_text())
    except BriefParseError as e:
        return {
            "spec": None,
            "gaps": [
                {
                    "kind": "format_error",
                    "location": "brief.md",
                    "description": str(e),
                    "severity": "blocking",
                }
            ],
        }

    existing = _load_spec(project_path)

    ticket_records = await tickets.list_all()

    def lookup(cap_id: str, aliases: list[str]) -> list[str]:
        wanted = {f"project://spec/capabilities/{cap_id}"}
        wanted.update(f"project://spec/capabilities/{a}" for a in aliases)
        return [t.id for t in ticket_records if t.derived_from in wanted]

    result = regenerate(
        brief=parsed_brief,
        existing=existing,
        ticket_lookup=lookup,
        now=datetime.now(timezone.utc),
    )

    spec_dict: dict | None = None
    if result.spec is not None:
        spec_dict = result.spec.model_dump(mode="json", by_alias=True)

    gap_dicts = [
        {
            "kind": g.kind,
            "location": g.location,
            "description": g.description,
            "severity": g.severity,
            "suggested_question": g.suggested_question,
        }
        for g in result.gaps
    ]
    return {"spec": spec_dict, "gaps": gap_dicts}
