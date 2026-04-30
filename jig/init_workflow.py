"""CLI coordinator for ``jig init <name>``.

Dispatches between fresh-init and resume, drives the PO / spec-gen /
SA conversation loops, and finalizes scaffold. v1: stub creation only —
PO/spec-gen/SA are wired in later tasks.
"""
from __future__ import annotations

import asyncio
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import yaml

if TYPE_CHECKING:
    from rich.console import Console

    from jig.init_prompts import PromptHandler

from jig.agent import run_agent
from jig.atomic import atomic_write_text
from jig.events import EventEmitter, JigEvent
from jig.logging_setup import configure_logging
from jig.persistence import load_role
from jig.project import Project, load_project, save_project
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.spec_generator import Gap, run_spec_generator
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.template_registry import list_templates, load_template_metadata
from jig.thread import Answer, Handoff, Note, Question, SystemEvent
from jig.ticket import Ticket, TicketStatus, WorkType


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
        # Distinguish "daemon touched .jig/ but init never ran" (FRESH)
        # from "init crashed mid-flow" (BROKEN). The former has only
        # daemon-managed subdirs (run/, logs/); the latter has actual
        # project state. Now that `jig` (no args) auto-spawns the daemon,
        # `.jig/run/`, `.jig/logs/`, `.jig/uploads/` can exist BEFORE init runs.
        _DAEMON_OWNED = {"run", "logs", "uploads"}
        contents = {p.name for p in (path / ".jig").iterdir()}
        if contents <= _DAEMON_OWNED:
            return DirState.FRESH
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
    """Create the minimal on-disk stub: ``.jig/project.yaml``,
    ``.jig/config.yaml``, and ``.jig/spec/project.md``. Idempotent: never
    overwrites an existing project.yaml or config.yaml.

    The lightweight ``project.yaml`` is the init-state marker read by
    ``classify_directory``; ``config.yaml`` is the runtime project config
    read by ``load_project`` once the workflow advances past stub creation.
    Both share the same ``id``.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / ".jig").mkdir(exist_ok=True)
    (path / ".jig" / "spec").mkdir(exist_ok=True)
    project_yaml = path / ".jig" / "project.yaml"
    if not project_yaml.is_file():
        project_id = str(uuid.uuid4())
        data = {
            "id": project_id,
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_text(project_yaml, yaml.safe_dump(data, sort_keys=False))
        save_project(
            path,
            Project(id=project_id, name=name, path=str(path.resolve())),
        )
    brief = path / ".jig" / "spec" / "project.md"
    if not brief.is_file():
        atomic_write_text(brief, f"# {name}\n")


async def run_init(
    *,
    name: str,
    force: bool,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
) -> None:
    """Top-level init flow. Dispatches fresh vs resume by classification."""
    from jig.init_prompts import CliPromptHandler, PromptHandler  # noqa: F401

    console = console or _spawn_console()
    prompts = prompts or CliPromptHandler()
    target = Path(name)
    # Derive the project's branding name from the path's basename so callers
    # can pass either a bare name ("mydogfood") or a fully resolved path
    # ("/foo/mydogfood") and get the same project name. The TUI's /init
    # passes resolved absolute paths so the daemon's cwd doesn't matter.
    project_name = target.name or name
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
        confirmed = await prompts.ask_force_confirm(target=target, console=console)
        if not confirmed:
            raise click.ClickException("Aborted.")
        shutil.rmtree(target / ".jig")

    create_stub(target, name=project_name)
    log_file = configure_logging(target, verbose=False, console=False)
    console.print(f"Logging to {log_file}", markup=False)
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
            _print_already_done(target, console=console)
            return
        if rs == ResumeState.BROKEN:
            raise click.ClickException(
                f"{target}/.jig is inconsistent. Use --force to reset."
            )
        if rs == ResumeState.PO_CONVERSATION:
            await run_po_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
                console=console,
            )
            continue
        if rs == ResumeState.NEEDS_ANSWER_BRIEF:
            await prompt_and_post_answers(
                tickets=tickets, threads=threads, bus=bus,
                ticket_id="brief",
                console=console,
                prompts=prompts,
            )
            continue
        if rs == ResumeState.BRIEF_APPROVAL:
            decision = await prompt_brief_approval(target, console=console, prompts=prompts)
            if decision == BriefApprovalChoice.YES:
                await threads.post(
                    SystemEvent(
                        ticket_id="brief", author="cli",
                        event_type="brief_approved",
                        content="operator approved brief",
                    )
                )
                continue
            if decision == BriefApprovalChoice.RESUME:
                brief = await tickets.get("brief")
                if brief is not None and brief.status == TicketStatus.RESOLVED:
                    await tickets.update("brief", status=TicketStatus.IN_PROGRESS)
                continue
            # NO
            console.print("Brief not approved. State saved.", markup=False)
            return
        if rs == ResumeState.SPEC_GENERATION:
            async with _cli_emitter("spec-generator", console=console) as emitter:
                await run_spec_generator(
                    project_path=target, tickets=tickets,
                    threads=threads, memory=memory, bus=bus,
                    emitter=emitter,
                )
            continue
        if rs == ResumeState.GAP_PROMPT:
            decision = await prompt_gap_decision(threads, console=console, prompts=prompts)
            if decision == "Q":
                console.print(
                    "State saved. Resume later with `jig init <name>`.",
                    markup=False,
                )
                return
            await run_po_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
                console=console,
            )
            continue
        if rs == ResumeState.BRANCH_PROMPT:
            choice = await prompt_branch_choice(console=console, prompts=prompts)
            if choice == BranchChoice.STAY:
                await run_po_conversation(
                    project_path=target, tickets=tickets,
                    threads=threads, memory=memory, bus=bus,
                    console=console,
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
                console=console,
            )
            continue
        if rs == ResumeState.SA_CONVERSATION:
            await run_sa_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
                console=console,
            )
            continue
        if rs == ResumeState.NEEDS_ANSWER_ARCH:
            await prompt_and_post_answers(
                tickets=tickets, threads=threads, bus=bus,
                ticket_id="architecture",
                console=console,
                prompts=prompts,
            )
            continue
        if rs == ResumeState.SA_CONFIRM_PROMPT:
            decision, proposal = await prompt_sa_confirm(threads, console=console, prompts=prompts)
            if decision == ConfirmChoice.NO:
                console.print("Scaffold cancelled. State saved.", markup=False)
                return
            if decision == ConfirmChoice.SWAP:
                # Re-spawn SA. SA reads the existing proposal from the
                # thread, treats it as the prior decision, and is
                # expected to post a NEW proposal — `latest_scaffold_proposal`
                # then returns that new one on the next iteration. If SA
                # exits without proposing again, the user sees the same
                # proposal and can hit `n` to abort. We do not record a
                # swap marker — the proposal sequence itself is the trail.
                await run_sa_conversation(
                    project_path=target, tickets=tickets,
                    threads=threads, memory=memory, bus=bus,
                    console=console,
                )
                continue
            # YES: scaffold with SA's proposal.
            await apply_scaffold(
                project_path=target,
                template_name=proposal["template_name"],
                sa_path=True,
                config=proposal.get("config", {}),
                tickets=tickets, threads=threads,
                console=console,
            )
            _print_summary(target, template_name=proposal["template_name"], console=console)
            return
        if rs == ResumeState.DIRECT_TEMPLATE_PICK:
            tpl = await prompt_direct_template(console=console, prompts=prompts)
            await apply_scaffold(
                project_path=target,
                template_name=tpl,
                sa_path=False,
                config=None,
                tickets=tickets, threads=threads,
                console=console,
            )
            _print_summary(target, template_name=tpl, console=console)
            return
        raise RuntimeError(f"unreachable resume state: {rs}")


def _print_already_done(target: Path, *, console: "Console | None" = None) -> None:
    c = console or _spawn_console()
    c.print(
        f"{target} is already initialized. Next: run `jig start` here.",
        markup=False,
    )


def _print_summary(
    target: Path, *, template_name: str, console: "Console | None" = None
) -> None:
    # The operator may have run `jig init <name>` from a parent directory,
    # so the project lives at `<cwd>/<target>`, not the cwd itself. The
    # `jig story` command defaults to `--path .` and would fail there;
    # surface the right invocation explicitly.
    c = console or _spawn_console()
    target_str = str(target)
    needs_path = target_str not in (".", "")
    path_arg = f" --path {target_str}" if needs_path else ""
    c.print(
        f"\n"
        f"Brief:        {target}/.jig/spec/project.md\n"
        f"Spec:         {target}/.jig/spec/project.structured.yaml\n"
        f"Architecture: {target}/.jig/spec/architecture.yaml\n"
        f"Template:     {template_name}\n\n"
        f"Setup log:    jig story brief{path_arg}\n"
        f"              jig story architecture{path_arg}\n",
        markup=False,
    )


async def run_po_conversation(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    console: "Console | None" = None,
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
    # On a respawn after a prior po_finish_brief (e.g. spec gaps came
    # back), the brief is in RESOLVED — a terminal status that would
    # make the agent loop exit immediately on its first poll. Reactivate
    # it so the new PO spawn actually runs.
    brief = await _reactivate_if_resolved(tickets, brief, author="cli")
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
    await _run_agent_with_cli_output(ctx, role_label="po", console=console)


async def _run_agent_with_cli_output(
    ctx: AgentSpawnContext, *, role_label: str, console: "Console | None" = None
) -> None:
    """Spawn ``run_agent(ctx)`` with a CLI-side emitter that streams
    text/tool/result events to stdout. Used by PO and SA spawn helpers."""
    async with _cli_emitter(role_label, console=console) as emitter:
        await run_agent(ctx, emitter=emitter)


def _spawn_console():
    """Lazily import + cache the rich Console used for spawn UI."""
    from rich.console import Console

    global _CONSOLE
    if _CONSOLE is None:
        _CONSOLE = Console()
    return _CONSOLE


_CONSOLE = None  # type: ignore[var-annotated]


@asynccontextmanager
async def _cli_emitter(role_label: str, *, console: "Console | None" = None):
    """Context manager that yields an ``EventEmitter`` and runs a rich
    Status spinner for the spawn duration.

    Visible UI during a spawn:
      - A Rule at the start carrying the role name.
      - A live "thinking…" spinner with elapsed seconds (refreshes in
        place; doesn't scroll).
      - Errors only — successful tool calls and agent narrative are
        hidden as duplicative noise. Errors print above the spinner.

    The spinner stops automatically when the spawn ends; the next
    structured prompt (question, gap report, branch choice) takes
    over the screen cleanly.
    """
    from rich.rule import Rule

    c = console or _spawn_console()
    emitter = EventEmitter()

    c.print()
    c.print(Rule(f"[bold cyan]{role_label}[/bold cyan]", style="cyan"))

    status_task = asyncio.create_task(_spawn_status(emitter, role_label, c))
    try:
        yield emitter
    finally:
        status_task.cancel()
        try:
            await status_task
        except asyncio.CancelledError:
            pass


async def _spawn_status(emitter: EventEmitter, role_label: str, console) -> None:
    """Emit a structured ``agent_thinking`` event once per second while a
    spawn is alive, plus surface real error events to the console.

    The CLI used to render a rich Status spinner here, but in daemon mode
    the spinner's ``\\r``-overwriting frames hit the streaming Console,
    became separate ``agent_render`` events on the wire, and got written
    as new scrollback lines (no in-place update). The TUI now renders the
    indicator from the structured event — Static widget that updates in
    place. The CLI path still works because the same event is logged
    (no visible spinner, but no crash either; init in CLI mode is
    rarely needed now that the TUI handles it).
    """
    queue = emitter.subscribe()
    loop = asyncio.get_event_loop()
    start = loop.time()
    try:
        # Announce we're thinking right away.
        await emitter.emit(
            JigEvent(
                type="agent_thinking",
                data={"role": role_label, "elapsed": 0, "active": True},
            )
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                elapsed = int(loop.time() - start)
                await emitter.emit(
                    JigEvent(
                        type="agent_thinking",
                        data={
                            "role": role_label,
                            "elapsed": elapsed,
                            "active": True,
                        },
                    )
                )
                continue
            # Skip our own emits to avoid a tight loop (emitter broadcasts
            # to all subscribers, including this one).
            if event.type == "agent_thinking":
                continue
            line = _format_event(event)
            if line is not None:
                console.print(line)
    finally:
        # Spawn ending: tell the TUI to hide the indicator.
        try:
            await emitter.emit(
                JigEvent(
                    type="agent_thinking",
                    data={"role": role_label, "elapsed": 0, "active": False},
                )
            )
        except Exception:
            pass
        try:
            emitter.unsubscribe(queue)
        except ValueError:
            pass


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


async def prompt_gap_decision(
    threads: ThreadStore,
    *,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
) -> str:
    """Display the gap prompt and return the user's decision ('R' or 'Q')."""
    from jig.init_prompts import CliPromptHandler, PromptHandler  # noqa: F401

    c = console or _spawn_console()
    p = prompts or CliPromptHandler()
    note = await latest_gap_note(threads)
    if note is None:
        raise RuntimeError("prompt_gap_decision called with no gap note")
    gaps = [Gap.model_validate(g) for g in note.payload["gaps"]]
    return await p.ask_gap_decision(gaps=gaps, console=c)


class BriefApprovalChoice(str, Enum):
    YES = "yes"
    RESUME = "resume"
    NO = "no"

    @classmethod
    def parse(cls, reply: str) -> "BriefApprovalChoice":
        r = reply.strip().lower()
        if r in ("", "y"):
            return cls.YES
        if r == "r":
            return cls.RESUME
        if r == "n":
            return cls.NO
        return cls.YES


def render_brief_for_approval(project_path: Path) -> str:
    """Read and render the brief markdown for approval display.

    Uses rich to render headings/bullets/bold as styled terminal output;
    falls back to raw text if the file is missing. Surrounds the
    rendering with visual separators so the operator can scan where the
    brief starts and ends.
    """
    brief_path = project_path / ".jig" / "spec" / "project.md"
    if not brief_path.is_file():
        body = "(brief is missing)"
    else:
        from io import StringIO

        from rich.console import Console
        from rich.markdown import Markdown

        buf = StringIO()
        # force_terminal=True so ANSI escapes are emitted even when stdout
        # is captured (e.g. piped or under monkeypatched click.echo);
        # the operator's terminal interprets them on display.
        console = Console(
            file=buf,
            force_terminal=True,
            color_system="truecolor",
            width=100,
        )
        console.print(Markdown(brief_path.read_text()))
        body = buf.getvalue().rstrip()
    return (
        "─── Brief preview ──────────────────────────────────────\n"
        f"{body}\n"
        "────────────────────────────────────────────────────────\n"
    )


async def prompt_brief_approval(
    project_path: Path,
    *,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
) -> BriefApprovalChoice:
    from jig.init_prompts import CliPromptHandler, PromptHandler  # noqa: F401

    c = console or _spawn_console()
    p = prompts or CliPromptHandler()
    return await p.ask_brief_approval(project_path=project_path, console=c)


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


async def prompt_branch_choice(
    *,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
) -> BranchChoice:
    from jig.init_prompts import CliPromptHandler, PromptHandler  # noqa: F401

    c = console or _spawn_console()
    p = prompts or CliPromptHandler()
    return await p.ask_branch_choice(console=c)


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
    *,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
) -> tuple[ConfirmChoice, dict]:
    from jig.init_prompts import CliPromptHandler, PromptHandler  # noqa: F401

    c = console or _spawn_console()
    p = prompts or CliPromptHandler()
    proposal = await latest_scaffold_proposal(threads)
    if proposal is None:
        raise RuntimeError("prompt_sa_confirm called with no proposal")
    choice = await p.ask_sa_confirm(
        template_name=proposal["template_name"],
        rationale=proposal["rationale"],
        console=c,
    )
    return choice, proposal


async def run_sa_conversation(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    console: "Console | None" = None,
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
    # Same reactivation logic as PO — after sa_propose_scaffold the
    # architecture ticket is RESOLVED; a `swap` re-spawn would die
    # on first poll without this flip.
    arch = await _reactivate_if_resolved(tickets, arch, author="cli")
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
    await _run_agent_with_cli_output(ctx, role_label="sa", console=console)


def render_template_list(names: list[str]) -> str:
    lines = ["Available templates:"]
    for i, n in enumerate(names, start=1):
        md = load_template_metadata(n)
        desc = md.description
        lines.append(f"  {i}) {n} — {desc}" if desc else f"  {i}) {n}")
    return "\n".join(lines)


async def prompt_direct_template(
    *,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
) -> str:
    from jig.init_prompts import CliPromptHandler, PromptHandler  # noqa: F401

    c = console or _spawn_console()
    p = prompts or CliPromptHandler()
    names = list_templates()
    return await p.ask_direct_template(template_names=names, console=c)


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
    console: "Console | None" = None,
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
    c = console or _spawn_console()
    from jig.hooks import HookInstallError, install_hooks
    try:
        install_hooks(project_path)
    except (HookInstallError, RuntimeError) as exc:
        c.print(f"Warning: hook install failed: {exc}", markup=False)

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


async def prompt_and_post_answers(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    ticket_id: str,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
) -> None:
    """Surface a ticket's open Questions to the operator, collect answers
    via stdin, persist them as Answer thread entries, and resume the ticket.

    Mirrors ``ws_server._handle_answer_questions`` semantics: one Answer
    per open Question (oldest-first), bus event per answer, then flip
    ``needs_info -> in_progress`` with a ``status_change`` SystemEvent
    and a ``ticket_updated`` bus event.
    """
    from jig.init_prompts import CliPromptHandler, PromptHandler  # noqa: F401

    ticket = await tickets.get(ticket_id)
    if ticket is None:
        raise RuntimeError(f"prompt_and_post_answers: missing ticket {ticket_id!r}")
    open_qs = await _open_questions(threads, ticket_id)
    if not open_qs:
        return

    c = console or _spawn_console()
    p = prompts or CliPromptHandler()
    for i, q in enumerate(open_qs, start=1):
        reply = await p.ask_question_answer(
            question=q,
            index=i,
            total=len(open_qs),
            console=c,
        )
        cid = await threads.post(
            Answer(
                ticket_id=ticket_id,
                author="user",
                question_id=q.id,
                text=reply,
            )
        )
        await bus.publish(
            Message(
                sender="user",
                to=ticket.assignee or "broadcast",
                type=MessageType.CONTEXT_UPDATE,
                payload={
                    "kind": "comment_posted",
                    "ticket_id": ticket_id,
                    "comment_id": cid,
                    "author": "user",
                    "content": reply,
                    "comment_kind": "answer",
                },
                topic=f"tickets.{ticket_id}",
            )
        )

    updated = await tickets.update(ticket_id, status=TicketStatus.IN_PROGRESS)
    await threads.post(
        SystemEvent(
            ticket_id=ticket_id,
            author="user",
            event_type="status_change",
            content=f"status needs_info -> {updated.status.value}",
        )
    )
    await bus.publish(
        Message(
            sender="user",
            to=updated.assignee or "broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "ticket_updated",
                "ticket_id": ticket_id,
                "status": updated.status.value,
            },
            topic=f"tickets.{ticket_id}",
        )
    )


def _display_tool_name(tool: str) -> str:
    """Strip the ``mcp__<server>__`` prefix for display.

    Operator transcript noise: every jig MCP tool is namespaced as
    ``mcp__jig__brief_set_section`` etc. The prefix conveys nothing
    useful at the CLI level — the operator already knows these are
    jig tools. Show the bare tool name instead.
    """
    if tool.startswith("mcp__"):
        # Format is mcp__<server>__<tool>; strip both prefix segments.
        rest = tool[len("mcp__"):]
        sep = rest.find("__")
        if sep != -1:
            return rest[sep + 2:]
    return tool


def _format_event(event: JigEvent) -> str | None:
    """Render an agent event for a CLI operator. Returns None to skip.

    Default UX: hide everything except errors. The operator sees the
    spawn boundary, the heartbeat, the question prompts, and the
    structured CLI prompts (branch, confirm, etc.) — agent narrative
    is duplicative noise (it often restates the question that's about
    to be prompted) and tool calls are implementation detail.
    """
    data = event.data or {}
    role = data.get("role", "?")
    if event.type == "agent_text":
        # Agent narrative is duplicative — its useful content (questions,
        # decisions) surfaces through structured CLI prompts. Hide.
        return None
    if event.type == "agent_tool":
        # Implementation noise — the agent's actions are visible via
        # the resulting CLI prompts (questions, gap reports, etc.).
        return None
    if event.type == "agent_tool_result":
        # Only surface failures.
        if not data.get("is_error"):
            return None
        tool = data.get("tool", "?")
        if tool == "ToolSearch":
            return None
        display_tool = _display_tool_name(tool)
        excerpt = (data.get("excerpt") or "").strip().splitlines()[0:1]
        err = excerpt[0] if excerpt else ""
        return (
            f"  ✗ {display_tool} ({role}): {err}"
            if err
            else f"  ✗ {display_tool} ({role})"
        )
    return None


class ResumeState(str, Enum):
    PO_CONVERSATION = "po_conversation"
    NEEDS_ANSWER_BRIEF = "needs_answer_brief"
    BRIEF_APPROVAL = "brief_approval"
    SPEC_GENERATION = "spec_generation"
    GAP_PROMPT = "gap_prompt"
    BRANCH_PROMPT = "branch_prompt"
    SA_CONVERSATION = "sa_conversation"
    NEEDS_ANSWER_ARCH = "needs_answer_arch"
    SA_CONFIRM_PROMPT = "sa_confirm_prompt"
    DIRECT_TEMPLATE_PICK = "direct_template_pick"
    ALREADY_DONE = "already_done"
    BROKEN = "broken"


async def _reactivate_if_resolved(
    tickets: TicketStore, ticket: Ticket, *, author: str
) -> Ticket:
    """Flip a ticket back to IN_PROGRESS if it was previously resolved.

    Init handoff handlers (po_finish_brief, sa_propose_scaffold, etc.)
    leave their ticket in RESOLVED so the agent loop notices it's done
    and exits. When the workflow respawns the same role (gap-prompt
    routes back to PO; the SA-confirm `swap` choice respawns SA), the
    new agent would otherwise see the still-RESOLVED ticket and exit
    on its first terminal-status poll. The reactivation is internal
    bookkeeping — no SystemEvent / bus event is posted because the
    only consumer is the next agent's poll loop.
    """
    del author  # reserved for future audit; not used today
    if ticket.status != TicketStatus.RESOLVED:
        return ticket
    return await tickets.update(ticket.id, status=TicketStatus.IN_PROGRESS)


async def _open_questions(threads: ThreadStore, ticket_id: str) -> list[Question]:
    """Return Questions on a ticket that still need a human answer, oldest-first.

    A Question is considered "needs answer" when:
      * its ``target`` is ``any_human`` (typed agent-to-agent questions
        from ``thread_ask`` are out of scope for the CLI prompt), AND
      * the asker hasn't marked it resolved, AND
      * no ``Answer`` entry references its id.

    The third clause matters because doc-08 says only the asker resolves
    a Question (``Question.is_resolved`` checks ``resolved_by``). Without
    it the init loop would re-prompt the user for already-answered
    questions on every PO/SA respawn.
    """
    entries = await threads.for_ticket(ticket_id)
    answered_ids = {
        e.question_id for e in entries if isinstance(e, Answer)
    }
    open_qs = [
        e for e in entries
        if isinstance(e, Question)
        and e.target == "any_human"
        and not e.is_resolved()
        and e.id not in answered_ids
    ]
    open_qs.sort(key=lambda q: q.created_at)
    return open_qs


async def _ticket_awaits_answer(
    tickets: TicketStore, threads: ThreadStore, ticket_id: str
) -> bool:
    """True iff ticket exists, is in needs_info, and has open Questions."""
    t = await tickets.get(ticket_id)
    if t is None or t.status != TicketStatus.NEEDS_INFO:
        return False
    return bool(await _open_questions(threads, ticket_id))


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

    if await _ticket_awaits_answer(tickets, threads, "brief"):
        return ResumeState.NEEDS_ANSWER_BRIEF

    brief_entries = await threads.for_ticket("brief")
    last_handoff_idx = -1
    last_gaps_event_idx = -1
    last_brief_approved_idx = -1
    has_spec_gen_event = False
    for i, e in enumerate(brief_entries):
        if isinstance(e, Handoff):
            last_handoff_idx = i
        elif isinstance(e, SystemEvent):
            if e.event_type == "spec_generated":
                has_spec_gen_event = True
            elif e.event_type == "spec_gaps_reported":
                last_gaps_event_idx = i
            elif e.event_type == "brief_approved":
                last_brief_approved_idx = i
    has_handoff = last_handoff_idx >= 0
    # Gaps are "fresh" only if reported after the most recent handoff. Once
    # PO re-handoffs after seeing the gap prompt, prior gaps are stale and
    # we should re-run the spec generator rather than re-prompting.
    gaps_after_handoff = last_gaps_event_idx > last_handoff_idx
    needs_approval = last_handoff_idx > last_brief_approved_idx

    # No Handoff and no spec_generated event: PO is still drafting the
    # brief. The spec_generated check guards a partial-write recovery
    # case where the Handoff didn't land but the downstream event did —
    # treat the brief as done in that case rather than looping back to PO.
    if not has_handoff and not has_spec_gen_event:
        return ResumeState.PO_CONVERSATION
    if gaps_after_handoff and not has_spec_gen_event:
        return ResumeState.GAP_PROMPT
    if has_handoff and needs_approval and not has_spec_gen_event:
        return ResumeState.BRIEF_APPROVAL
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
    if await _ticket_awaits_answer(tickets, threads, "architecture"):
        return ResumeState.NEEDS_ANSWER_ARCH
    return ResumeState.SA_CONVERSATION


