"""CLI coordinator for ``jig init <name>``.

Dispatches between fresh-init and resume, drives the PO / spec-gen /
SA conversation loops, and finalizes scaffold. v1: stub creation only —
PO/spec-gen/SA are wired in later tasks.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import logging

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

logger = logging.getLogger(__name__)


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
    ``.jig/config.yaml``, and ``docs/brief.md``. Idempotent: never
    overwrites an existing project.yaml or config.yaml.

    The lightweight ``project.yaml`` is the init-state marker read by
    ``classify_directory``; ``config.yaml`` is the runtime project config
    read by ``load_project`` once the workflow advances past stub creation.
    Both share the same ``id``.

    Also runs ``git init`` if the directory is not already inside a git
    repository, so hooks and worktrees work regardless of whether the
    user came via ``jig create`` or ``/init`` directly.
    """
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    (path / ".jig").mkdir(exist_ok=True)
    (path / "docs").mkdir(exist_ok=True)

    # Initialise a git repo if one doesn't already exist here.
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=str(path),
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        try:
            subprocess.run(
                ["git", "init", "-q"],
                cwd=str(path),
                check=True,
                capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass  # no git available — hooks won't work, but init proceeds
    project_yaml = path / ".jig" / "project.yaml"
    if not project_yaml.is_file():
        project_id = str(uuid.uuid4())
        data = {
            "id": project_id,
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_text(project_yaml, yaml.safe_dump(data, sort_keys=False))
        # Detect the actual default branch (HEAD) of the host repo so
        # worktrees branch off the right base. Without this we always
        # write "main" and worktree creation fails on repos using
        # "develop", "master", "trunk", etc.
        from jig.persistence import _detect_git_branch

        default_branch = _detect_git_branch(path)
        save_project(
            path,
            Project(
                id=project_id,
                name=name,
                path=str(path.resolve()),
                default_branch=default_branch,
            ),
        )
    brief = path / "docs" / "brief.md"
    if not brief.is_file():
        atomic_write_text(brief, f"# {name}\n")


async def run_init(
    *,
    name: str,
    force: bool,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
    brief_file: Path | None = None,
    profile_name: str | None = None,
) -> None:
    """Top-level init flow. Dispatches fresh vs resume by classification.

    When ``brief_file`` is set, the file is copied to ``docs/brief.md``
    and the brief ticket is pre-resolved with a Handoff + brief_approved
    SystemEvent so ``classify_resume`` skips ``PO_CONVERSATION`` and
    falls straight into ``SPEC_GENERATION``. Used by ``jig init --brief``
    for eval harnesses.

    When ``profile_name`` is set (from ``jig init --profile``), the
    named profile is applied AFTER the ``--force`` cleanup and
    canonical ``create_stub`` — applying it earlier would race
    ``shutil.rmtree(target / ".jig")`` and lose the write. The profile
    is the eval / auto-mode bypass for the PM-1 selection pass.
    """
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
    # Snapshot operator-authored profile/workflow YAMLs across the
    # ``--force`` rmtree. ``--force --profile <custom>`` would otherwise
    # delete ``.jig/profiles/<custom>.yaml`` before ``load_profile``
    # could resolve it; the same blast radius silently erases an
    # operator's local override of a shipped profile name.
    preserved_profiles: dict[str, str] = {}
    preserved_workflows: dict[str, str] = {}
    if force and (target / ".jig").is_dir():
        confirmed = await prompts.ask_force_confirm(target=target, console=console)
        if not confirmed:
            raise click.ClickException("Aborted.")
        for src in (target / ".jig" / "profiles").glob("*.yaml"):
            preserved_profiles[src.name] = src.read_text(encoding="utf-8")
        for src in (target / ".jig" / "workflows").glob("*.yaml"):
            preserved_workflows[src.name] = src.read_text(encoding="utf-8")
        shutil.rmtree(target / ".jig")

    create_stub(target, name=project_name)
    # Restore the snapshot (no-op if no force happened or no project-
    # local YAMLs existed). Operator edits to shipped names survive
    # the force, and operator-only profile / workflow YAMLs are not
    # silently lost.
    if preserved_profiles:
        dest_dir = target / ".jig" / "profiles"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for fname, body in preserved_profiles.items():
            (dest_dir / fname).write_text(body, encoding="utf-8")
    if preserved_workflows:
        dest_dir = target / ".jig" / "workflows"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for fname, body in preserved_workflows.items():
            (dest_dir / fname).write_text(body, encoding="utf-8")
    # ``--profile`` bypass: write the profile to config AFTER the
    # canonical ``create_stub`` (so ``.jig/config.yaml`` exists) and
    # AFTER any ``--force`` cleanup (so the rmtree doesn't delete the
    # write). ``classify_resume`` then sees ``cfg.profile.name``
    # populated on the next tick and skips ``PM_PROFILE_PASS``.
    #
    # Inlined here rather than calling ``cli._apply_profile_at_start``
    # to keep the dependency direction CLI → workflow and route the
    # status message through the caller-supplied console.
    if profile_name is not None:
        from jig.config import load_config, save_config
        from jig.profile_loader import (
            apply_profile,
            copy_profile_templates,
            load_profile,
        )

        try:
            profile = load_profile(profile_name, project_path=target)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc)) from exc
        cfg = apply_profile(load_config(target), profile)
        save_config(target, cfg)
        copy_profile_templates(profile, target)
        console.print(
            f"Applied profile '{profile.name}' (sa_role={profile.sa_role})",
            markup=False,
        )
    log_file = configure_logging(target, verbose=False, console=False)
    # Render the path relative to the project so the line fits within
    # the TUI's scrollback width without wrapping. The streaming
    # console renders at fixed width=120 and the TUI's RichLog
    # re-wraps at its actual (narrower) width, and the double-wrap
    # corrupts the absolute path mid-filename. Relative form is
    # ~35 chars vs ~90+ for the absolute form — fits cleanly.
    try:
        log_display = log_file.relative_to(target)
    except ValueError:
        # Shouldn't happen with the current ``configure_logging``
        # impl — it hardcodes ``project_path / ".jig" / "logs"``,
        # so ``log_file`` is always under ``target``. Defensive
        # fallback for any future change that adds a custom log
        # directory option.
        log_display = log_file
    console.print(
        f"[dim]Logging to {log_display}[/dim]",
        markup=True,
        highlight=False,
    )
    store_dir = target / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    memory = MemoryStore(store_dir)
    bus = MessageBus(store_dir / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    # Announce every direct ``tickets.create(...)`` on the broadcast
    # topic so the TUI (if running) sees full ticket payloads rather
    # than partial status-event merges.
    from jig.ticket_events import wire_create_publisher

    wire_create_publisher(tickets, bus, sender="init")

    try:
        await _run_init_resume_loop(
            target=target,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
            prompts=prompts,
            console=console,
            brief_file=brief_file,
        )
    finally:
        # ``wire_create_publisher`` schedules each broadcast as a
        # background task. Without draining, ``asyncio.run`` would
        # cancel any task still in flight when this function returns,
        # silently dropping the broadcasts the TUI relies on.
        await tickets.drain_background_tasks()


async def _run_init_resume_loop(
    *,
    target: Path,
    tickets: TicketStore,
    threads: "ThreadStore",
    memory: "MemoryStore",
    bus: "MessageBus",
    prompts: "PromptHandler",
    console: "Console",
    brief_file: Path | None,
) -> None:
    """Resume-state dispatch loop extracted from ``run_init``.

    Kept separate so ``run_init`` owns the try/finally that drains
    the wire-up's background publish tasks before returning.
    """
    # --brief: seed a baked brief and skip the PO conversation. Idempotent —
    # if the brief ticket already exists (e.g. on a re-run without --force),
    # the seed is a no-op and the resume loop picks up where we left off.
    if brief_file is not None:
        await _seed_baked_brief(
            project_path=target,
            brief_file=brief_file,
            tickets=tickets,
            threads=threads,
            bus=bus,
            console=console,
        )

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
                project_path=target,
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                console=console,
            )
            continue
        if rs == ResumeState.NEEDS_ANSWER_BRIEF:
            await prompt_and_post_answers(
                tickets=tickets,
                threads=threads,
                bus=bus,
                ticket_id="brief",
                console=console,
                prompts=prompts,
            )
            continue
        if rs == ResumeState.BRIEF_APPROVAL:
            decision = await prompt_brief_approval(
                target, console=console, prompts=prompts
            )
            if decision == BriefApprovalChoice.YES:
                await threads.post(
                    SystemEvent(
                        ticket_id="brief",
                        author="cli",
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
            async with _cli_emitter(
                "Spec Generator",
                console=console,
                subtitle="Generating structured spec from docs/brief.md",
            ) as emitter:
                await run_spec_generator(
                    project_path=target,
                    tickets=tickets,
                    threads=threads,
                    memory=memory,
                    bus=bus,
                    emitter=emitter,
                )
            continue
        if rs == ResumeState.GAP_PROMPT:
            decision = await prompt_gap_decision(
                threads, console=console, prompts=prompts
            )
            if decision == "Q":
                console.print(
                    "State saved. Resume later with `jig init <name>`.",
                    markup=False,
                )
                return
            await run_po_conversation(
                project_path=target,
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                console=console,
            )
            continue
        if rs == ResumeState.BRANCH_PROMPT:
            choice = await prompt_branch_choice(console=console, prompts=prompts)
            if choice == BranchChoice.STAY:
                await run_po_conversation(
                    project_path=target,
                    tickets=tickets,
                    threads=threads,
                    memory=memory,
                    bus=bus,
                    console=console,
                )
                continue
            if choice == BranchChoice.DIRECT:
                await create_sa_skipped_marker(tickets=tickets, threads=threads)
                continue
            # SA: create ticket (if needed) and run.
            await run_sa_conversation(
                project_path=target,
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                console=console,
            )
            continue
        if rs == ResumeState.PM_PROFILE_PASS:
            await run_pm_profile_pass(
                project_path=target,
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                console=console,
            )
            continue
        if rs == ResumeState.PM_PROFILE_CONFIRM_PROMPT:
            decision, proposal = await prompt_profile_confirm(
                threads, console=console, prompts=prompts
            )
            if decision == ConfirmChoice.NO:
                console.print("Profile not approved. State saved.", markup=False)
                return
            # YES accepts PM's choice; SWAP flips to the other profile.
            # Both branches apply directly — no PM re-spawn (only two
            # profiles ship, so the workflow can flip without consulting
            # the agent again).
            chosen_name = proposal["name"]
            if decision == ConfirmChoice.SWAP:
                # SWAP is hard-wired to flip between the two shipped
                # profiles. ``handle_pm_propose_profile`` only accepts
                # ``small`` or ``medium``, so the toggle is well-defined.
                # A real runtime check (not ``assert`` — that's compiled
                # out under ``-O``) catches any future drift between
                # the MCP allowlist and this branch.
                if chosen_name not in {"small", "medium"}:
                    raise click.ClickException(
                        f"SWAP requires a shipped profile name; got "
                        f"{chosen_name!r}. handle_pm_propose_profile "
                        "must restrict PM-1 to small/medium."
                    )
                chosen_name = "small" if chosen_name == "medium" else "medium"
            from jig.config import load_config, save_config
            from jig.profile_loader import (
                apply_profile,
                copy_profile_templates,
                load_profile,
            )

            try:
                profile = load_profile(chosen_name, project_path=target)
            except FileNotFoundError as exc:
                # Shipped profiles don't disappear in practice, but a
                # malformed install or partial-removal would land here.
                # Surface as a friendly ClickException for consistency
                # with ``_apply_profile_at_start``.
                raise click.ClickException(str(exc)) from exc
            try:
                cfg = apply_profile(load_config(target), profile)
            except FileNotFoundError as exc:
                raise click.ClickException(
                    f"{target}/.jig/config.yaml not found while applying "
                    f"profile {chosen_name!r}. Init state is inconsistent — "
                    "re-run `jig init`."
                ) from exc
            save_config(target, cfg)
            copy_profile_templates(profile, target)
            console.print(
                f"Applied profile '{profile.name}' "
                f"(sa_role={profile.sa_role}). Next: SA.",
                markup=False,
            )
            continue
        if rs == ResumeState.SA_CONVERSATION:
            await run_sa_conversation(
                project_path=target,
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                console=console,
            )
            continue
        if rs == ResumeState.NEEDS_ANSWER_ARCH:
            await prompt_and_post_answers(
                tickets=tickets,
                threads=threads,
                bus=bus,
                ticket_id="architecture",
                console=console,
                prompts=prompts,
            )
            continue
        if rs == ResumeState.SA_CONFIRM_PROMPT:
            decision, proposal = await prompt_sa_confirm(
                threads, console=console, prompts=prompts
            )
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
                    project_path=target,
                    tickets=tickets,
                    threads=threads,
                    memory=memory,
                    bus=bus,
                    console=console,
                )
                continue
            # YES: scaffold with SA's proposal.
            await apply_scaffold(
                project_path=target,
                template_name=proposal["template_name"],
                sa_path=True,
                config=proposal.get("decisions", proposal.get("config", {})),
                constraints=proposal.get("constraints", []),
                open_questions=proposal.get("open_questions", []),
                tech_decisions=proposal.get("tech_decisions", []),
                size=proposal.get("size", "S"),
                tickets=tickets,
                threads=threads,
                console=console,
            )
            _print_summary(
                target, template_name=proposal["template_name"], console=console
            )
            await _create_planning_ticket(tickets, target)
            await prompts.ask_init_complete(console=console)
            return
        if rs == ResumeState.DIRECT_TEMPLATE_PICK:
            tpl = await prompt_direct_template(console=console, prompts=prompts)
            await apply_scaffold(
                project_path=target,
                template_name=tpl,
                sa_path=False,
                config=None,
                tickets=tickets,
                threads=threads,
                console=console,
            )
            _print_summary(target, template_name=tpl, console=console)
            await _create_planning_ticket(tickets, target)
            await prompts.ask_init_complete(console=console)
            return
        raise RuntimeError(f"unreachable resume state: {rs}")


async def _create_planning_ticket(tickets: TicketStore, project_path: Path) -> None:
    """Create the planning ticket if one doesn't already exist.

    Description surfaces what the chosen template has already shipped so
    the PM (which lacks file-read tools, per `feedback_pm_no_file_tools`)
    knows not to create scaffolding/skeleton tickets that duplicate
    template output.
    """
    existing = await tickets.get("planning")
    if existing is not None:
        return
    spec_path = project_path / ".jig" / "spec" / "project.structured.yaml"
    description_parts = [
        "Break down the project spec into implementation tickets.",
        "",
        f"Spec: {spec_path}",
    ]
    scaffold_section = _scaffold_summary_for_pm(project_path)
    if scaffold_section:
        description_parts.extend(["", scaffold_section])
    description = "\n".join(description_parts)
    await tickets.create(
        Ticket(
            id="planning",
            work_type=WorkType.PLANNING,
            title="Project planning",
            description=description,
            workflow="project",
            created_by="cli",
        )
    )


def _scaffold_summary_for_pm(project_path: Path) -> str:
    """Render the 'already scaffolded' inventory the PM consumes via ticket
    description. Reads ``.jig/spec/architecture.yaml`` written by
    ``apply_scaffold``. Returns an empty string when the file is missing,
    is malformed, isn't a mapping, or lacks a ``template`` field —
    callers skip the section when the result is empty."""
    arch_path = project_path / ".jig" / "spec" / "architecture.yaml"
    if not arch_path.is_file():
        return ""
    try:
        data = yaml.safe_load(arch_path.read_text())
    except yaml.YAMLError:
        return ""
    # yaml.safe_load on a top-level list / string / scalar returns a
    # non-mapping; .get() would raise AttributeError. Skip — the file
    # is syntactically valid but not the expected shape, treat same as
    # missing.
    if not isinstance(data, dict):
        return ""
    template = data.get("template")
    if not template:
        return ""
    lines = [
        "## Already scaffolded — do NOT plan tickets for these",
        "",
        f"The `{template}` template has already been applied. The following",
        "are in place on disk; planning a 'project setup' or 'skeleton' ticket",
        "would duplicate work. Start your plan with the first real capability.",
        "",
        f"- **Template**: `{template}`",
    ]
    language = data.get("language")
    if language:
        lines.append(f"- **Language**: {language}")
    framework = data.get("framework")
    if framework:
        lines.append(f"- **Framework**: {framework}")
    decisions = data.get("decisions")
    if isinstance(decisions, dict) and decisions:
        lines.append("- **Architecture decisions** (already recorded):")
        for key, value in decisions.items():
            lines.append(f"  - `{key}`: {value}")
    # Grounded tech decisions (Phase 1) — coexists with the legacy
    # `decisions` dict above during the migration. Surface each choice with
    # its provenance, and flag any `inferred` (ungrounded) decision so the
    # PM sees what wasn't verified against a source. Soft signal — warns,
    # does not block init.
    tech_decisions = data.get("tech_decisions")
    if isinstance(tech_decisions, list) and tech_decisions:
        lines.append("- **Grounded tech decisions** (with sources):")
        inferred_ids: list[str] = []
        for td in tech_decisions:
            if not isinstance(td, dict):
                continue
            td_id = td.get("id", "?")
            choice = td.get("choice", "?")
            source_type = td.get("source_type", "?")
            source_ref = td.get("source_ref")
            ref_suffix = f" ({source_ref})" if source_ref else ""
            lines.append(f"  - `{td_id}`: {choice} — {source_type}{ref_suffix}")
            if source_type == "inferred":
                inferred_ids.append(td_id)
        if inferred_ids:
            inferred_list = ", ".join(f"`{i}`" for i in inferred_ids)
            lines.extend(
                [
                    "",
                    "- ⚠️ **Ungrounded decisions** (source_type: inferred — no "
                    f"external source was verified): {inferred_list}. Treat these "
                    "as assumptions to confirm, not settled facts.",
                ]
            )
    lines.extend(
        [
            "",
            "Every shipped template provides: project layout, `pyproject.toml`",
            "with pytest/ruff/mypy configured, build-system, `.gitignore`,",
            "README stub, and a passing smoke test. Shape-specific templates",
            "additionally provide their characteristic shell code (e.g.",
            "`python-cli` ships the typer entry + injectable httpx transport;",
            "`fastapi` ships the FastAPI app + `/health` endpoint).",
        ]
    )
    return "\n".join(lines)


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

    # Use plain styled markup rather than a Rich Panel so the rendering
    # is robust against ANSI-roundtrip width mismatch (the daemon's
    # console width is typically 80; the TUI's scrollback is wider, so
    # box-drawing characters get clipped or repeated).
    c.print()
    c.print("[bold black on green] ✓ Init complete [/bold black on green]")
    c.print()
    c.print(f"  [bold cyan]Brief[/bold cyan]         {target}/docs/brief.md")
    c.print(
        f"  [bold cyan]Spec[/bold cyan]          {target}/.jig/spec/project.structured.yaml"
    )
    c.print(
        f"  [bold cyan]Architecture[/bold cyan]  {target}/.jig/spec/architecture.yaml"
    )
    c.print(
        f"  [bold cyan]Template[/bold cyan]      "
        f"[bright_green]{template_name}[/bright_green]"
    )
    c.print()
    c.print(f"  [dim]Setup log[/dim]     jig story brief{path_arg}")
    c.print(f"               jig story architecture{path_arg}")
    c.print()
    c.print(
        "  Run [bold]jig start[/bold] (or type [bold]/status[/bold]) "
        "to begin orchestration."
    )
    c.print()


async def _seed_baked_brief(
    *,
    project_path: Path,
    brief_file: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    console: "Console",
) -> None:
    """Pre-seed a brief from disk so classify_resume skips PO_CONVERSATION.

    Steps (all idempotent):
      1. Copy ``brief_file`` to ``docs/brief.md`` if not already there.
      2. Create the ``brief`` ticket if it doesn't exist.
      3. Post a Handoff (phase=spec-generator) and a ``brief_approved``
         SystemEvent so classify_resume falls through to SPEC_GENERATION.
      4. Resolve the brief ticket.

    On a re-run (e.g. resuming after spec-gen had gaps) any of these
    steps may already be done; the function detects that and skips.
    """
    from jig.handoff_resolve import resolve_after_handoff as _resolve_after_handoff

    brief_path = project_path / "docs" / "brief.md"
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    src_text = brief_file.read_text()
    if not brief_path.is_file() or brief_path.read_text() != src_text:
        atomic_write_text(brief_path, src_text)
        console.print(
            f"[dim]Loaded brief from {brief_file}[/dim]",
            markup=True,
            highlight=False,
        )

    brief = await tickets.get("brief")
    if brief is None:
        brief = Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="Project brief",
            created_by="cli",
        )
        await tickets.create(brief)

    entries = await threads.for_ticket("brief")
    has_handoff = any(isinstance(e, Handoff) for e in entries)
    has_approved = any(
        isinstance(e, SystemEvent) and e.event_type == "brief_approved" for e in entries
    )

    if not has_handoff:
        await threads.post(
            Handoff(
                ticket_id="brief",
                author="cli",
                phase="spec-generator",
                outputs=["docs/brief.md"],
                summary="Brief loaded from --brief.",
            )
        )
        await bus.publish(
            Message(
                sender="cli",
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
            author="cli",
        )

    if not has_approved:
        await threads.post(
            SystemEvent(
                ticket_id="brief",
                author="cli",
                event_type="brief_approved",
                content="brief auto-approved (--brief)",
            )
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
    await _run_agent_with_cli_output(
        ctx,
        role_label="Product Owner",
        console=console,
        subtitle="Refining the project brief with the operator",
    )


async def _run_agent_with_cli_output(
    ctx: AgentSpawnContext,
    *,
    role_label: str,
    console: "Console | None" = None,
    subtitle: str | None = None,
) -> None:
    """Spawn ``run_agent(ctx)`` with a CLI-side emitter that streams
    text/tool/result events to stdout. Used by PO and SA spawn helpers."""
    async with _cli_emitter(role_label, console=console, subtitle=subtitle) as emitter:
        await run_agent(ctx, emitter=emitter)


def _spawn_console():
    """Lazily import + cache the rich Console used for spawn UI."""
    from rich.console import Console

    global _CONSOLE
    if _CONSOLE is None:
        _CONSOLE = Console()
    return _CONSOLE


_CONSOLE = None  # type: ignore[var-annotated]
_BACKGROUND_TASKS: set[asyncio.Task] = set()


@asynccontextmanager
async def _cli_emitter(
    role_label: str,
    *,
    console: "Console | None" = None,
    subtitle: str | None = None,
):
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
    c = console or _spawn_console()
    emitter = EventEmitter()

    # If the console is a streaming console, its backing EventEmitter is
    # stashed as _jig_event_emitter. Pass it to _spawn_status so that
    # agent_thinking events reach the TUI's Activity subzone.
    tui_emitter = getattr(c, "_jig_event_emitter", None)

    if tui_emitter is not None:
        # TUI mode: emit a structured event so the TUI can render a
        # native full-width banner rather than a pre-baked ANSI string.
        # Pass a subtitle (what this agent is working on) so the banner
        # has a "Working on: …" line beneath the role name — operators
        # then see what's happening, not just "an agent started."
        from jig.events import JigEvent

        loop = asyncio.get_running_loop()
        data = {"role": role_label}
        if subtitle:
            data["ticket_title"] = subtitle
        _t = loop.create_task(tui_emitter.emit(JigEvent(type="agent_start", data=data)))
        _BACKGROUND_TASKS.add(_t)

        def _on_done(t: asyncio.Task) -> None:
            _BACKGROUND_TASKS.discard(t)
            if not t.cancelled() and (exc := t.exception()):
                logger.error("TUI emit task raised: %s", exc, exc_info=exc)

        _t.add_done_callback(_on_done)
    else:
        from rich.rule import Rule

        c.print()
        c.print(Rule(f"[bold cyan]{role_label}[/bold cyan]", style="cyan"))

    status_task = asyncio.create_task(
        _spawn_status(emitter, role_label, c, tui_emitter=tui_emitter)
    )
    try:
        yield emitter
    finally:
        status_task.cancel()
        try:
            await status_task
        except asyncio.CancelledError:
            pass


async def _spawn_status(
    emitter: EventEmitter,
    role_label: str,
    console,
    *,
    tui_emitter: "EventEmitter | None" = None,
) -> None:
    """Emit a structured ``agent_thinking`` event once per second while a
    spawn is alive, plus surface real error events to the console.

    ``tui_emitter``, when provided, receives agent_thinking events in
    addition to the local emitter so the TUI's Activity subzone updates.
    """
    queue = emitter.subscribe()
    loop = asyncio.get_event_loop()
    start = loop.time()

    async def _emit_thinking(data: dict) -> None:
        ev = JigEvent(type="agent_thinking", data=data)
        await emitter.emit(ev)
        if tui_emitter is not None:
            try:
                await tui_emitter.emit(ev)
            except Exception:
                pass

    try:
        await _emit_thinking({"role": role_label, "elapsed": 0, "active": True})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                elapsed = int(loop.time() - start)
                await _emit_thinking(
                    {"role": role_label, "elapsed": elapsed, "active": True}
                )
                continue
            if event.type == "agent_thinking":
                continue
            line = _format_event(event)
            if line is not None:
                console.print(line)
    finally:
        try:
            await _emit_thinking({"role": role_label, "elapsed": 0, "active": False})
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
    gap_notes = [e for e in entries if isinstance(e, Note) and "gaps" in e.payload]
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
    brief_path = project_path / "docs" / "brief.md"
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
            width=72,
        )
        # Strip pandoc-style heading anchors ({#id}) that Rich doesn't
        # understand and would render as literal text.
        text = re.sub(r"\s*\{#[^}]+\}", "", brief_path.read_text())
        console.print(Markdown(text))
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
    # The options (Y / p / s) are rendered by the TUI's prompt panel as
    # button-style badges; emit only the context line here so we don't
    # duplicate them in scrollback.
    return "Brief accepted."


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


def render_sa_confirm_prompt(
    *,
    template_name: str,
    rationale: str,
    tech_decisions: list[dict] | None = None,
    size: str = "S",
) -> str:
    # Options (Y/n/swap) come from the prompt panel; emit only the
    # informational context here so it doesn't duplicate in scrollback.
    # Plain text — the CLI path uses ``console.print(..., markup=False)``
    # and the TUI path wraps via ``Markdown(...)``. Rich markup tags
    # would show through literally in both. Size is shown read-only —
    # operator override is Phase 2 scope.
    tech_decisions = tech_decisions or []
    lines = [
        f"SA proposes: {template_name}  (project size: {size})",
        "",
        "Rationale:",
        rationale,
    ]
    if tech_decisions:
        lines.extend(
            [
                "",
                "Grounded tech decisions:",
                f"  {'ID':<18}{'CHOICE':<16}{'SOURCE':<20}REF",
            ]
        )
        for td in tech_decisions:
            td_id = str(td.get("id", "?"))
            choice = str(td.get("choice", "?"))
            source_type = str(td.get("source_type", "?"))
            source_ref = str(td.get("source_ref") or "—")
            lines.append(f"  {td_id:<18}{choice:<16}{source_type:<20}{source_ref}")
    return "\n".join(lines)


async def latest_scaffold_proposal(threads: ThreadStore) -> dict | None:
    """Return the payload of the most recent ``sa_propose_scaffold``
    Note on the architecture ticket, or ``None`` if none exists.
    """
    entries = await threads.for_ticket("architecture")
    proposals = [
        e
        for e in entries
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
        tech_decisions=proposal.get("tech_decisions", []),
        size=proposal.get("size", "S"),
        console=c,
    )
    return choice, proposal


async def latest_profile_proposal(threads: ThreadStore) -> dict | None:
    """Return the payload of the most recent ``pm_propose_profile`` Note
    on the profile ticket, or ``None`` if none exists.

    Mirrors ``latest_scaffold_proposal`` — the most-recent proposal wins
    when multiple PM-1 passes happened (swap path doesn't re-spawn the
    PM today, but a future change might).
    """
    entries = await threads.for_ticket("profile")
    proposals = [
        e
        for e in entries
        if isinstance(e, Note) and e.payload.get("kind") == "pm_propose_profile"
    ]
    if not proposals:
        return None
    return dict(proposals[-1].payload)


def render_profile_confirm_prompt(*, name: str, rationale: str) -> str:
    """Short summary of the PM's profile choice, shown in scrollback
    before the confirm prompt. Options (Y / swap / n) come from the
    prompt panel — emit only the informational context here.

    Plain text only: the CLI path uses ``console.print(..., markup=False)``
    and the TUI path wraps via ``Markdown(...)``. Rich markup tags
    don't render in either, so they'd show through literally.
    """
    return f"PM proposes profile: {name}\n\nRationale:\n{rationale}"


async def prompt_profile_confirm(
    threads: ThreadStore,
    *,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
) -> tuple[ConfirmChoice, dict]:
    """Confirm prompt for the PM's profile proposal — mirrors ``prompt_sa_confirm``.

    Returns the operator's choice + the proposal payload so the caller
    can apply it (or flip to the other profile on SWAP) without
    re-reading the thread.
    """
    from jig.init_prompts import CliPromptHandler, PromptHandler  # noqa: F401

    c = console or _spawn_console()
    p = prompts or CliPromptHandler()
    proposal = await latest_profile_proposal(threads)
    if proposal is None:
        raise RuntimeError("prompt_profile_confirm called with no proposal")
    choice = await p.ask_profile_confirm(
        name=proposal["name"],
        rationale=proposal["rationale"],
        console=c,
    )
    return choice, proposal


async def run_pm_profile_pass(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    console: "Console | None" = None,
) -> None:
    """Create (if needed) the profile ticket and spawn PM-1.

    PM-1 reads the brief + structured spec, picks a profile, calls
    ``pm_propose_profile`` exactly once, and exits. The init resume
    loop picks up the proposal on the next iteration and routes to
    ``PM_PROFILE_CONFIRM_PROMPT``.
    """
    profile_ticket = await tickets.get("profile")
    if profile_ticket is None:
        profile_ticket = Ticket(
            id="profile",
            work_type=WorkType.PROFILE,
            title="Pick project profile",
            created_by="cli",
        )
        await tickets.create(profile_ticket)
    profile_ticket = await _reactivate_if_resolved(
        tickets, profile_ticket, author="cli"
    )
    project = load_project(project_path)
    role_cfg = load_role(project_path, "pm")
    ctx = AgentSpawnContext(
        role="pm",
        role_cfg=role_cfg,
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=profile_ticket,
        parent=None,
        worktree_path=project_path,
        project=project,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )
    await _run_agent_with_cli_output(
        ctx,
        role_label="Project Manager (profile selection)",
        subtitle="Picking project profile from the brief",
        console=console,
    )


def _resolve_sa_role(project_path: Path) -> str:
    """Return the SA role id for this project — profile-driven.

    Reads ``cfg.profile.sa_role`` from ``.jig/config.yaml`` when set;
    falls back to ``"sa"`` when the project predates project-profiles
    (empty string default) or when config is missing entirely. The
    fallback preserves the current behaviour for legacy projects.
    """
    from jig.config import load_config

    try:
        cfg = load_config(project_path)
    except FileNotFoundError:
        return "sa"
    role_id = cfg.profile.sa_role.strip()
    return role_id if role_id else "sa"


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
    sa_role_id = _resolve_sa_role(project_path)
    role_cfg = load_role(project_path, sa_role_id)
    ctx = AgentSpawnContext(
        role=sa_role_id,
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
    await _run_agent_with_cli_output(
        ctx,
        role_label="Solutions Architect",
        console=console,
        subtitle="Picking template + framing the architecture",
    )


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


def _commit_scaffold(
    project_path: Path,
    template_name: str,
    *,
    console: "Console | None" = None,
) -> None:
    """Commit any uncommitted scaffold output on the host branch.

    Ensures ``.jig/`` is gitignored before staging so daemon state never
    enters the project's git history. Best-effort: a failure here logs a
    note but does not block init.
    """
    import os
    import subprocess

    c = console or _spawn_console()

    # Make sure .jig/ is in .gitignore so the daemon's runtime state
    # doesn't get committed alongside the scaffold.
    gitignore = project_path / ".gitignore"
    existing = gitignore.read_text() if gitignore.is_file() else ""
    to_add = [entry for entry in (".jig/",) if entry not in existing.splitlines()]
    if to_add:
        prefix = existing.rstrip() + "\n" if existing.strip() else ""
        atomic_write_text(gitignore, prefix + "\n".join(to_add) + "\n")

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(project_path),
            check=True,
            capture_output=True,
            env=env,
        )
        # Nothing staged → nothing to commit (idempotent re-run).
        diff_check = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(project_path),
            capture_output=True,
            env=env,
        )
        if diff_check.returncode == 0:
            return
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"chore: initial scaffold from jig init ({template_name})",
                "--no-verify",
            ],
            cwd=str(project_path),
            check=True,
            capture_output=True,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode(errors="ignore").strip()
        c.print(
            f"Note: scaffold commit skipped ({stderr or exc})",
            markup=False,
        )
    except Exception as exc:  # noqa: BLE001
        c.print(f"Note: scaffold commit skipped ({exc})", markup=False)


async def apply_scaffold(
    *,
    project_path: Path,
    template_name: str,
    sa_path: bool,
    config: dict[str, Any] | None,
    constraints: list[str] | None = None,
    open_questions: list[dict] | None = None,
    tech_decisions: list[dict] | None = None,
    size: str = "S",
    tickets: TicketStore,
    threads: ThreadStore,
    console: "Console | None" = None,
) -> None:
    """Copy the template, finalize architecture.yaml, update project.yaml,
    install git hooks (best-effort), and emit scaffold_applied.
    """
    tech_decisions = tech_decisions or []
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
    arch_file.parent.mkdir(parents=True, exist_ok=True)
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
    if sa_path:
        if config:
            data["decisions"] = config
        if constraints:
            data["constraints"] = constraints
        if open_questions:
            data["open_questions"] = open_questions
        # `size` is written unconditionally on the SA path (even when SA
        # produced no tech_decisions) so Phase 2 can read
        # arch_get_field("size"); the Note payload is consumed during init
        # and isn't accessible post-confirm. `tech_decisions` is written
        # only when non-empty.
        data["size"] = size
        if tech_decisions:
            data["tech_decisions"] = tech_decisions
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
        c.print(f"Note: git hook install skipped ({exc})", markup=False)

    # 5. Commit the scaffold so the host branch is a clean baseline for
    # future ticket merges. Without this commit, every PM-created ticket
    # that touches a scaffolded file fails its merge with
    # "main checkout has uncommitted changes" because the host tree is
    # full of untracked scaffold output.
    _commit_scaffold(project_path, template_name, console=console)

    # 6. Ensure architecture ticket exists, then emit scaffold_applied.
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
        rest = tool[len("mcp__") :]
        sep = rest.find("__")
        if sep != -1:
            return rest[sep + 2 :]
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
    PM_PROFILE_PASS = "pm_profile_pass"
    PM_PROFILE_CONFIRM_PROMPT = "pm_profile_confirm_prompt"
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
    answered_ids = {e.question_id for e in entries if isinstance(e, Answer)}
    open_qs = [
        e
        for e in entries
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

    # Spec generated. Insert the PM-1 profile-selection pass before SA
    # so SA can read ``cfg.profile.sa_role`` and run as the right role.
    # Three gates:
    #
    # * ``cfg.profile.name`` already set (``--profile`` flag bypass, or
    #   the operator confirmed a prior PM-1 proposal) → skip.
    # * No ``pm_propose_profile`` Note on the profile ticket → spawn PM-1.
    # * Proposal exists but profile still unset → operator gate.
    from jig.config import load_config

    try:
        cfg = load_config(project_path)
    except FileNotFoundError:
        cfg = None
    profile_name = cfg.profile.name.strip() if cfg is not None else ""
    if not profile_name:
        profile_ticket = await tickets.get("profile")
        has_profile_proposal = False
        if profile_ticket is not None:
            profile_entries = await threads.for_ticket("profile")
            has_profile_proposal = any(
                isinstance(e, Note) and e.payload.get("kind") == "pm_propose_profile"
                for e in profile_entries
            )
        if not has_profile_proposal:
            return ResumeState.PM_PROFILE_PASS
        return ResumeState.PM_PROFILE_CONFIRM_PROMPT

    # Spec generated + profile set. Now look at architecture ticket.
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
    # sa_mvp exits via arch_finalize, which posts Handoff(phase="pm") without
    # a sa_propose_scaffold Note. Treat this as SA-done so the dispatch loop
    # stops respawning SA. Exclude rejected handoffs so an evaluator rejection
    # falls through to SA_CONVERSATION and SA is respawned.
    has_arch_finalize_handoff = any(
        isinstance(e, Handoff) and e.phase == "pm" and e.acceptance_state != "rejected"
        for e in arch_entries
    )

    if has_scaffold_applied:
        return ResumeState.ALREADY_DONE
    if has_sa_skipped:
        return ResumeState.DIRECT_TEMPLATE_PICK
    if has_proposal:
        return ResumeState.SA_CONFIRM_PROMPT
    if has_arch_finalize_handoff:
        return ResumeState.ALREADY_DONE
    if await _ticket_awaits_answer(tickets, threads, "architecture"):
        return ResumeState.NEEDS_ANSWER_ARCH
    return ResumeState.SA_CONVERSATION
