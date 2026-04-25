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
from typing import Any

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
from jig.template_registry import list_templates, load_template_metadata
from jig.thread import Handoff, Note, SystemEvent
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
    """Top-level init flow. Dispatches fresh vs resume by classification."""
    target = Path(name)
    ds = classify_directory(target)
    if ds == DirState.ALREADY_DONE and not force:
        raise click.ClickException(
            f"{target} already initialized. Use --force to restart from scratch."
        )
    if ds == DirState.BROKEN and not force:
        raise click.ClickException(
            f"{target}/.jig is in an inconsistent state. Use --force to reset."
        )
    if force and (target / ".jig").is_dir():
        _confirm_force(target)
        shutil.rmtree(target / ".jig")

    create_stub(target, name=name)
    store_dir = target / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    memory = MemoryStore(store_dir)
    bus = MessageBus(store_dir / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()

    # Iterate: each pass classifies the resume state and advances one step.
    while True:
        rs = await classify_resume(
            project_path=target, tickets=tickets, threads=threads
        )
        if rs == ResumeState.ALREADY_DONE:
            _print_already_done(target)
            return
        if rs == ResumeState.BROKEN:
            raise click.ClickException(
                f"{target}/.jig is inconsistent. Use --force to reset."
            )
        if rs == ResumeState.PO_CONVERSATION:
            await run_po_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.SPEC_GENERATION:
            from jig.spec_generator import run_spec_generator
            await run_spec_generator(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.GAP_PROMPT:
            decision = await prompt_gap_decision(threads)
            if decision == "Q":
                click.echo("State saved. Resume later with `jig init <name>`.")
                return
            await run_po_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.BRANCH_PROMPT:
            choice = await prompt_branch_choice()
            if choice == BranchChoice.STAY:
                await run_po_conversation(
                    project_path=target, tickets=tickets,
                    threads=threads, memory=memory, bus=bus,
                )
                continue
            if choice == BranchChoice.DIRECT:
                await create_sa_skipped_marker(
                    tickets=tickets, threads=threads
                )
                continue
            # SA: create ticket (if needed) and run.
            await run_sa_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.SA_CONVERSATION:
            await run_sa_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.SA_CONFIRM_PROMPT:
            decision, proposal = await prompt_sa_confirm(threads)
            assert proposal is not None
            if decision == ConfirmChoice.NO:
                click.echo("Scaffold cancelled. State saved.")
                return
            if decision == ConfirmChoice.SWAP:
                # Re-spawn SA; the existing proposal remains in the
                # thread so SA sees the prior decision.
                await run_sa_conversation(
                    project_path=target, tickets=tickets,
                    threads=threads, memory=memory, bus=bus,
                )
                continue
            # YES: scaffold with SA's proposal.
            await apply_scaffold(
                project_path=target,
                template_name=proposal["template_name"],
                sa_path=True,
                config=proposal.get("config", {}),
                tickets=tickets, threads=threads,
            )
            _print_summary(target, template_name=proposal["template_name"])
            return
        if rs == ResumeState.DIRECT_TEMPLATE_PICK:
            tpl = await prompt_direct_template()
            await apply_scaffold(
                project_path=target,
                template_name=tpl,
                sa_path=False,
                config=None,
                tickets=tickets, threads=threads,
            )
            _print_summary(target, template_name=tpl)
            return
        raise RuntimeError(f"unreachable resume state: {rs}")


def _print_already_done(target: Path) -> None:
    click.echo(
        f"{target} is already initialized. Next: run `jig start` here."
    )


def _print_summary(target: Path, *, template_name: str) -> None:
    click.echo(
        f"\n"
        f"Brief:        {target}/.jig/spec/project.md\n"
        f"Spec:         {target}/.jig/spec/project.structured.yaml\n"
        f"Architecture: {target}/.jig/spec/architecture.yaml\n"
        f"Template:     {template_name}\n\n"
        f"Setup log:    jig story brief\n"
        f"              jig story architecture\n"
    )


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


def render_template_list(names: list[str]) -> str:
    lines = ["Available templates:"]
    for i, n in enumerate(names, start=1):
        md = load_template_metadata(n)
        desc = md.description
        lines.append(f"  {i}) {n} — {desc}" if desc else f"  {i}) {n}")
    return "\n".join(lines)


async def prompt_direct_template() -> str:
    names = list_templates()
    while True:
        click.echo(render_template_list(names))
        reply = click.prompt(
            f"Pick (1-{len(names)})", default="1", show_default=False
        ).strip()
        try:
            idx = int(reply)
        except ValueError:
            click.echo("Please enter a number.")
            continue
        if 1 <= idx <= len(names):
            return names[idx - 1]
        click.echo("Out of range.")


async def create_sa_skipped_marker(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
) -> None:
    arch = await tickets.get("architecture")
    if arch is None:
        arch = Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="Architecture",
            created_by="cli",
        )
        await tickets.create(arch)
    await threads.post(
        SystemEvent(
            ticket_id="architecture",
            author="cli",
            event_type="sa_skipped",
            content="user chose direct-pick",
        )
    )


def _apply_template_files(
    *,
    template_name: str,
    dest: Path,
    project_name: str,
) -> None:
    """Copy a project template into dest, substituting 'myproject'
    with a sanitized project_name. Skips template.yaml metadata.
    """
    tpl_root = Path(__file__).resolve().parent / "defaults" / "project_templates"
    tpl_dir = tpl_root / template_name
    if not tpl_dir.is_dir():
        raise KeyError(f"unknown template: {template_name!r}")
    pkg_name = project_name.replace("-", "_").replace(" ", "_").lower()
    skip_dirs = {
        "__pycache__",
        ".ruff_cache",
        ".mypy_cache",
        ".pytest_cache",
        ".venv",
        "node_modules",
    }
    for src in tpl_dir.rglob("*"):
        if not src.is_file():
            continue
        if src.name == "template.yaml":
            continue
        rel = src.relative_to(tpl_dir)
        if skip_dirs & set(rel.parts):
            continue
        rel_renamed = Path(str(rel).replace("myproject", pkg_name))
        dest_file = dest / rel_renamed
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        raw = src.read_bytes()
        try:
            text = raw.decode()
            dest_file.write_text(text.replace("myproject", pkg_name))
        except UnicodeDecodeError:
            dest_file.write_bytes(raw)


async def apply_scaffold(
    *,
    project_path: Path,
    template_name: str,
    sa_path: bool,
    config: dict[str, Any] | None,
    tickets: TicketStore,
    threads: ThreadStore,
) -> None:
    """Copy the template, finalize architecture.yaml, update project.yaml,
    install git hooks (best-effort), and emit scaffold_applied.
    """
    md = load_template_metadata(template_name)
    applied_at = datetime.now(timezone.utc).isoformat()

    # 1. Copy template files.
    _apply_template_files(
        template_name=template_name,
        dest=project_path,
        project_name=project_path.name,
    )

    # 2. Finalize architecture.yaml — preserve any SA-authored fields.
    arch_file = project_path / ".jig" / "spec" / "architecture.yaml"
    if arch_file.is_file():
        data = yaml.safe_load(arch_file.read_text()) or {}
    else:
        data = {}
    data["template"] = template_name
    data["template_applied_at"] = applied_at
    data["sa_path"] = sa_path
    data.setdefault("language", md.language)
    if md.framework is not None:
        data.setdefault("framework", md.framework)
    if md.deploy_target is not None:
        data.setdefault("deploy_target", md.deploy_target)
    if sa_path and config is not None:
        data["config"] = config
    atomic_write_text(arch_file, yaml.safe_dump(data, sort_keys=False))

    # 3. Update project.yaml with template_name and template_applied_at.
    project_yaml = project_path / ".jig" / "project.yaml"
    pdata = yaml.safe_load(project_yaml.read_text()) or {}
    pdata["template_name"] = template_name
    pdata["template_applied_at"] = applied_at
    atomic_write_text(project_yaml, yaml.safe_dump(pdata, sort_keys=False))

    # 4. Best-effort install git hooks. If the project isn't a git repo
    #    or hook install refuses, that's a soft failure — print a warning
    #    and continue. Hooks are dev-loop parity; scaffold completion
    #    must not depend on them. ``install_hooks`` calls ``_git_common_dir``
    #    which raises ``RuntimeError`` when ``project_path`` isn't a real
    #    git repo, so catch both error types here as the single source of
    #    truth for soft-failure semantics.
    from jig.hooks import HookInstallError, install_hooks
    try:
        install_hooks(project_path)
    except (HookInstallError, RuntimeError) as exc:
        click.echo(f"Warning: hook install failed: {exc}")

    # 5. Ensure architecture ticket exists, then emit scaffold_applied.
    arch = await tickets.get("architecture")
    if arch is None:
        await tickets.create(
            Ticket(
                id="architecture",
                work_type=WorkType.ARCHITECTURE,
                title="Architecture",
                created_by="cli",
            )
        )
    await threads.post(
        SystemEvent(
            ticket_id="architecture",
            author="cli",
            event_type="scaffold_applied",
            content=f"template={template_name}",
        )
    )


class ResumeState(str, Enum):
    PO_CONVERSATION = "po_conversation"
    SPEC_GENERATION = "spec_generation"
    GAP_PROMPT = "gap_prompt"
    BRANCH_PROMPT = "branch_prompt"
    SA_CONVERSATION = "sa_conversation"
    SA_CONFIRM_PROMPT = "sa_confirm_prompt"
    DIRECT_TEMPLATE_PICK = "direct_template_pick"
    ALREADY_DONE = "already_done"
    BROKEN = "broken"


async def classify_resume(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
) -> ResumeState:
    """Pure inspection. Maps persisted state to the next action."""
    # Short-circuit on already-done / broken via directory state.
    ds = classify_directory(project_path)
    if ds == DirState.ALREADY_DONE:
        return ResumeState.ALREADY_DONE
    if ds == DirState.BROKEN:
        return ResumeState.BROKEN

    brief = await tickets.get("brief")
    if brief is None:
        return ResumeState.PO_CONVERSATION

    brief_entries = await threads.for_ticket("brief")
    has_handoff = any(isinstance(e, Handoff) for e in brief_entries)
    has_spec_gen_event = any(
        isinstance(e, SystemEvent) and e.event_type == "spec_generated"
        for e in brief_entries
    )
    has_gaps_event = any(
        isinstance(e, SystemEvent) and e.event_type == "spec_gaps_reported"
        for e in brief_entries
    )

    # No Handoff and no spec_generated event: PO is still drafting the
    # brief. The spec_generated check guards a partial-write recovery
    # case where the Handoff didn't land but the downstream event did —
    # treat the brief as done in that case rather than looping back to PO.
    if not has_handoff and not has_spec_gen_event:
        return ResumeState.PO_CONVERSATION
    if has_gaps_event and not has_spec_gen_event:
        return ResumeState.GAP_PROMPT
    if not has_spec_gen_event:
        return ResumeState.SPEC_GENERATION

    # Spec generated. Now look at architecture ticket.
    arch = await tickets.get("architecture")
    if arch is None:
        return ResumeState.BRANCH_PROMPT

    arch_entries = await threads.for_ticket("architecture")
    has_sa_skipped = any(
        isinstance(e, SystemEvent) and e.event_type == "sa_skipped"
        for e in arch_entries
    )
    has_scaffold_applied = any(
        isinstance(e, SystemEvent) and e.event_type == "scaffold_applied"
        for e in arch_entries
    )
    has_proposal = any(
        isinstance(e, Note) and e.payload.get("kind") == "sa_propose_scaffold"
        for e in arch_entries
    )

    if has_scaffold_applied:
        return ResumeState.ALREADY_DONE
    if has_sa_skipped:
        return ResumeState.DIRECT_TEMPLATE_PICK
    if has_proposal:
        return ResumeState.SA_CONFIRM_PROMPT
    return ResumeState.SA_CONVERSATION


def _confirm_force(target: Path) -> None:
    reply = click.prompt(
        f"This will wipe {target}/.jig. Type 'force' to continue",
        default="",
        show_default=False,
    )
    if reply != "force":
        raise click.ClickException("Aborted.")
