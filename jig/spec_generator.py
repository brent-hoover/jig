"""Spec-generator agent scaffolding.

The spec-generator is a one-shot, non-conversational agent that
translates the brief into the structured spec and validates it. This
module defines the Gap payload model; the spawn wrapper is added in a
later task.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from jig.agent import run_agent
from jig.persistence import load_role
from jig.project import load_project
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore

if TYPE_CHECKING:
    from jig.events import EventEmitter


class Gap(BaseModel):
    """A brief-validation finding reported by the spec-generator."""

    kind: Literal["missing", "contradiction", "ambiguity", "under_specified"]
    location: str
    description: str
    suggested_question: str | None = None
    severity: Literal["blocking", "advisory"]


async def run_spec_generator(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    emitter: "EventEmitter | None" = None,
) -> None:
    """Spawn the one-shot spec-generator agent on the brief ticket.

    Returns when the agent process exits. The agent is responsible for
    calling ``spec_publish`` or ``spec_report_gaps`` before exit; if it
    exits without either, resume logic re-runs it on the next init.

    The spec-generator runs against the real project directory (not an
    isolated worktree) because it reads ``.jig/spec/project.md`` and
    writes the structured spec back into the project tree.

    ``emitter`` is forwarded to ``run_agent`` so the init CLI can
    surface the agent's tool calls and text turns to the operator —
    without it the spec-generator runs invisibly and the operator
    can't tell whether it's working or hung.
    """
    brief = await tickets.get("brief")
    if brief is None:
        raise KeyError("brief")

    project = load_project(project_path)
    role_cfg = load_role(project_path, "spec-generator")

    ctx = AgentSpawnContext(
        role="spec-generator",
        role_cfg=role_cfg,
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=brief,
        parent=None,
        worktree_path=project_path,
        project=project,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )
    await run_agent(ctx, emitter=emitter)
