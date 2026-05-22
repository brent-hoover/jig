"""MCP tool handlers for the L0 PO (Track B1, bones).

The L0 PO captures pitch + problem + audience + product-level non-goals
in a 3-5 turn conversation, then calls ``l0_finalize`` to write both
``brief.md`` (markdown form per ``docs/v2.0/multi-level-spec/design.md``
§"L0 — Pitch") and ``project.structured.yaml`` (Pydantic dump of
``jig.schemas.po.Project``).

Distinct from ``init_mcp.py`` (v1 monolithic-brief path). Both will
coexist until the rest of Track B lands and v1 is removed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jig.atomic import atomic_write_text
from jig.handoff_resolve import resolve_after_handoff as _resolve_after_handoff
from jig.schemas.po import ProductNonGoal, Project
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff

L0_TICKET_ID = "project"


def _project_md_path(project_path: Path) -> Path:
    return project_path / "docs" / "brief.md"


def _project_structured_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "project.structured.yaml"


def render_project_md(project: Project) -> str:
    """Render an L0 ``Project`` to the markdown form per design.md.

    Format:

        # <name>

        <pitch>

        ## Problem

        <problem>

        ## Audience

        <audience>

        ## Non-goals (product-level)

        - {#<id>} <text> — <rationale>
        ...
    """
    lines: list[str] = []
    lines.append(f"# {project.name}")
    lines.append("")
    lines.append(project.pitch)
    lines.append("")
    lines.append("## Problem")
    lines.append("")
    lines.append(project.problem)
    lines.append("")
    lines.append("## Audience")
    lines.append("")
    lines.append(project.audience)
    lines.append("")
    lines.append("## Non-goals (product-level)")
    lines.append("")
    for ng in project.non_goals:
        if ng.rationale:
            lines.append(f"- {{#{ng.id}}} {ng.text} — {ng.rationale}")
        else:
            lines.append(f"- {{#{ng.id}}} {ng.text}")
    # Always end with a single trailing newline.
    return "\n".join(lines) + "\n"


def _coerce_non_goals(raw: list[Any]) -> list[ProductNonGoal]:
    out: list[ProductNonGoal] = []
    for entry in raw:
        if isinstance(entry, ProductNonGoal):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(
                f"non_goal entry must be a dict, got {type(entry).__name__}"
            )
        try:
            out.append(ProductNonGoal.model_validate(entry))
        except ValidationError as e:
            raise ValueError(f"invalid non_goal entry: {e}") from e
    return out


async def handle_l0_finalize(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    name: str,
    pitch: str,
    problem: str,
    audience: str,
    non_goals: list[Any],
    author: str,
) -> str:
    """Construct the L0 ``Project``, write both artifacts, hand off to L1.

    Returns the Handoff entry id. Raises ``ValueError`` if the inputs
    don't validate against the ``Project`` schema (blank fields, malformed
    non-goals, etc.) — the agent prompt instructs the PO to fill all
    four fields before calling.
    """
    coerced = _coerce_non_goals(non_goals)
    try:
        project = Project(
            name=name,
            pitch=pitch,
            problem=problem,
            audience=audience,
            non_goals=coerced,
        )
    except ValidationError as e:
        raise ValueError(f"L0 project does not validate: {e}") from e

    md = render_project_md(project)
    atomic_write_text(_project_md_path(project_path), md)

    yaml_text = yaml.safe_dump(
        project.model_dump(mode="json"),
        sort_keys=False,
    )
    atomic_write_text(_project_structured_path(project_path), yaml_text)

    handoff = Handoff(
        ticket_id=L0_TICKET_ID,
        author=author,
        phase="po-l1",
        outputs=[
            "docs/brief.md",
            ".jig/spec/project.structured.yaml",
        ],
        summary=f"L0 captured: {project.name}",
    )
    entry_id = await threads.post(handoff)
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "handoff_posted",
                "ticket_id": L0_TICKET_ID,
                "phase": "po-l1",
            },
            topic="orchestrator",
        )
    )
    await _resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id=L0_TICKET_ID,
        author=author,
    )
    return entry_id
