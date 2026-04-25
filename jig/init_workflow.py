"""CLI coordinator for ``jig init <name>``.

Dispatches between fresh-init and resume, drives the PO / spec-gen /
SA conversation loops, and finalizes scaffold. v1: stub creation only —
PO/spec-gen/SA are wired in later tasks.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import click
import yaml

from jig.agent import run_agent
from jig.atomic import atomic_write_text
from jig.persistence import load_role
from jig.project import load_project
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.spec_generator import Gap
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note
from jig.ticket import Ticket, WorkType


class DirState(str, Enum):
    FRESH = "fresh"
    IN_PROGRESS = "in_progress"
    ALREADY_DONE = "already_done"
    BROKEN = "broken"


def classify_directory(path: Path) -> DirState:
    """Inspect ``path`` and decide which branch of init to run.

    Pure function — no side effects.
    """
    if not path.exists() or not (path / ".jig").is_dir():
        return DirState.FRESH
    project_yaml = path / ".jig" / "project.yaml"
    if not project_yaml.is_file():
        return DirState.BROKEN
    try:
        data = yaml.safe_load(project_yaml.read_text()) or {}
    except yaml.YAMLError:
        return DirState.BROKEN
    if not isinstance(data, dict):
        return DirState.BROKEN
    if not {"id", "name", "created_at"} <= data.keys():
        return DirState.BROKEN
    if data.get("template_applied_at"):
        return DirState.ALREADY_DONE
    return DirState.IN_PROGRESS


def create_stub(path: Path, *, name: str) -> None:
    """Create the minimal on-disk stub: ``.jig/project.yaml`` and
    ``.jig/spec/project.md``. Idempotent: never overwrites an existing
    project.yaml or brief.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / ".jig").mkdir(exist_ok=True)
    (path / ".jig" / "spec").mkdir(exist_ok=True)
    project_yaml = path / ".jig" / "project.yaml"
    if not project_yaml.is_file():
        data = {
            "id": str(uuid.uuid4()),
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_text(project_yaml, yaml.safe_dump(data, sort_keys=False))
    brief = path / ".jig" / "spec" / "project.md"
    if not brief.is_file():
        atomic_write_text(brief, f"# {name}\n")


async def run_init(*, name: str, force: bool) -> None:
    """Top-level init flow. Fleshed out across subsequent tasks.

    At this task stage, ``run_init`` only handles fresh / already-done /
    broken branches and creates the stub. PO spawn, spec-gen, branch
    prompt, SA, and scaffold are wired in later tasks.
    """
    target = Path(name)
    state = classify_directory(target)
    if state == DirState.ALREADY_DONE and not force:
        raise click.ClickException(
            f"{target} already initialized. Use --force to restart from scratch."
        )
    if state == DirState.BROKEN and not force:
        raise click.ClickException(
            f"{target}/.jig is in an inconsistent state. Use --force to reset."
        )
    if force and (target / ".jig").is_dir():
        _confirm_force(target)
        shutil.rmtree(target / ".jig")
    create_stub(target, name=name)
    click.echo(f"Initialized stub at {target}/.jig")
    # Later tasks wire the rest of the flow here.


async def run_po_conversation(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
) -> None:
    """Create (if needed) the brief ticket and spawn the PO agent on it.

    The PO agent drives the conversation via its MCP tools; when it
    calls ``po_finish_brief`` the agent process exits cleanly.
    """
    brief = await tickets.get("brief")
    if brief is None:
        brief = Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="Project brief",
            created_by="cli",
        )
        await tickets.create(brief)
    project = load_project(project_path)
    role_cfg = load_role(project_path, "po")
    ctx = AgentSpawnContext(
        role="po",
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
    await run_agent(ctx)


async def latest_gap_note(threads: ThreadStore) -> Note | None:
    """Return the most recent Gap-bearing Note on the brief ticket,
    or None if no gaps have been reported.
    """
    entries = await threads.for_ticket("brief")
    gap_notes = [
        e for e in entries
        if isinstance(e, Note) and "gaps" in e.payload
    ]
    if not gap_notes:
        return None
    return gap_notes[-1]


def render_gap_prompt(gaps: list[Gap]) -> str:
    lines = ["Spec generation found gaps in the brief:"]
    for g in gaps:
        lines.append(f"  - [{g.severity}] {g.location}: {g.description}")
    lines.append("")
    lines.append("[R] Resume PO conversation to address  (default)")
    lines.append("[Q] Quit (state saved; resume later with `jig init <name>`)")
    return "\n".join(lines)


async def prompt_gap_decision(threads: ThreadStore) -> str:
    """Display the gap prompt and return the user's decision ('R' or 'Q')."""
    note = await latest_gap_note(threads)
    if note is None:
        raise RuntimeError("prompt_gap_decision called with no gap note")
    gaps = [Gap.model_validate(g) for g in note.payload["gaps"]]
    click.echo(render_gap_prompt(gaps))
    reply = click.prompt("Choice", default="R", show_default=False).strip().upper()
    if reply not in ("R", "Q"):
        reply = "R"
    return reply


class BranchChoice(str, Enum):
    SA = "sa"
    DIRECT = "direct"
    STAY = "stay"

    @classmethod
    def parse(cls, reply: str) -> "BranchChoice":
        r = reply.strip().lower()
        if r in ("", "y"):
            return cls.SA
        if r == "p":
            return cls.DIRECT
        if r == "s":
            return cls.STAY
        return cls.SA


def render_branch_prompt() -> str:
    return (
        "Brief accepted. Choose your path:\n"
        "  [Y] Hand off to SA for architecture + template  (default)\n"
        "  [p] Pick a template yourself from the list\n"
        "  [s] Stay on PO — brief needs more work\n"
    )


async def prompt_branch_choice() -> BranchChoice:
    click.echo(render_branch_prompt())
    reply = click.prompt("Choice", default="Y", show_default=False)
    return BranchChoice.parse(reply)


class ConfirmChoice(str, Enum):
    YES = "yes"
    NO = "no"
    SWAP = "swap"

    @classmethod
    def parse(cls, reply: str) -> "ConfirmChoice":
        r = reply.strip().lower()
        if r in ("", "y"):
            return cls.YES
        if r == "n":
            return cls.NO
        if r == "swap":
            return cls.SWAP
        return cls.YES


def render_sa_confirm_prompt(*, template_name: str, rationale: str) -> str:
    return (
        f"SA proposes: {template_name}\n\n"
        f"Rationale:\n{rationale}\n\n"
        "[Y/n/swap]  (Y = accept, n = cancel, swap = re-consult SA)"
    )


async def latest_scaffold_proposal(threads: ThreadStore) -> dict | None:
    """Return the payload of the most recent ``sa_propose_scaffold``
    Note on the architecture ticket, or ``None`` if none exists.
    """
    entries = await threads.for_ticket("architecture")
    proposals = [
        e for e in entries
        if isinstance(e, Note) and e.payload.get("kind") == "sa_propose_scaffold"
    ]
    if not proposals:
        return None
    return dict(proposals[-1].payload)


async def prompt_sa_confirm(
    threads: ThreadStore,
) -> tuple[ConfirmChoice, dict | None]:
    proposal = await latest_scaffold_proposal(threads)
    if proposal is None:
        raise RuntimeError("prompt_sa_confirm called with no proposal")
    click.echo(render_sa_confirm_prompt(
        template_name=proposal["template_name"],
        rationale=proposal["rationale"],
    ))
    reply = click.prompt("Choice", default="Y", show_default=False)
    return ConfirmChoice.parse(reply), proposal


async def run_sa_conversation(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
) -> None:
    """Create (if needed) the architecture ticket and spawn the SA agent."""
    arch = await tickets.get("architecture")
    if arch is None:
        arch = Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="Architecture",
            created_by="cli",
        )
        await tickets.create(arch)
    project = load_project(project_path)
    role_cfg = load_role(project_path, "sa")
    ctx = AgentSpawnContext(
        role="sa",
        role_cfg=role_cfg,
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=arch,
        parent=None,
        worktree_path=project_path,
        project=project,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )
    await run_agent(ctx)


def _confirm_force(target: Path) -> None:
    reply = click.prompt(
        f"This will wipe {target}/.jig. Type 'force' to continue",
        default="",
        show_default=False,
    )
    if reply != "force":
        raise click.ClickException("Aborted.")
