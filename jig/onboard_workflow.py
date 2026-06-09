"""CLI coordinator for ``jig onboard <path>``.

Imports an existing codebase into the jig workflow: a scanner pass
produces ``.jig/onboard/observations.md``, the PO extracts the
current-state brief from existing behavior, the spec generator runs
unchanged, and the PM picks a profile. Phase 2 (SA read pass, operator
review, PM backlog) is blocked on sa-architect Phase 2 — those states
raise ``NotImplementedError``.

The pattern mirrors ``init_workflow.py``: a while loop calls
``classify_onboard_resume()`` each tick and dispatches the next step.
``classify_onboard_resume()`` is a standalone pure function — it does
not call ``classify_resume()`` and shares no dispatch logic with the
greenfield flow.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import logging

import click
import yaml

if TYPE_CHECKING:
    from rich.console import Console

    from jig.init_prompts import PromptHandler

from jig.atomic import atomic_write_text
from jig.init_workflow import (
    DirState,
    classify_directory,
    create_stub,
    _spawn_console,
    _ticket_awaits_answer,
)
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff, Note, SystemEvent

logger = logging.getLogger(__name__)


class OnboardResumeState(str, Enum):
    SCAN_PASS = "scan_pass"
    PO_READ_PASS = "po_read_pass"
    NEEDS_ANSWER_BRIEF = "needs_answer_brief"
    PO_REVIEW = "po_review"
    SPEC_PASS = "spec_pass"
    PM_PROFILE_PASS = "pm_profile_pass"
    PM_PROFILE_CONFIRM_PROMPT = "pm_profile_confirm_prompt"
    SA_READ_PASS = "sa_read_pass"
    NEEDS_ANSWER_ARCH = "needs_answer_arch"
    OPERATOR_REVIEW = "operator_review"
    PM_BACKLOG = "pm_backlog"
    ALREADY_DONE = "already_done"
    BROKEN = "broken"


def _onboard_dir(project_path: Path) -> Path:
    return project_path / ".jig" / "onboard"


def _read_project_yaml(project_path: Path) -> dict:
    project_yaml = project_path / ".jig" / "project.yaml"
    if not project_yaml.is_file():
        return {}
    try:
        data = yaml.safe_load(project_yaml.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def is_onboard_project(project_path: Path) -> bool:
    """True iff ``project.yaml`` carries the onboard-flow marker."""
    return bool(_read_project_yaml(project_path).get("onboard_started_at"))


async def classify_onboard_resume(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
) -> OnboardResumeState:
    """Pure inspection. Maps persisted onboard state to the next action.

    Implements all transitions up through PM_PROFILE_CONFIRM_PROMPT.
    The Phase-2 states (SA_READ_PASS → PM_BACKLOG) raise
    ``NotImplementedError`` until sa-architect Phase 2 freezes the
    unified SA role interface.
    """
    ds = classify_directory(project_path)
    if ds == DirState.BROKEN:
        return OnboardResumeState.BROKEN
    # ``template_applied_at`` set means a *greenfield* init completed
    # here — onboarding never sets it. That's not an onboard state at
    # all; resuming an onboard over it is inconsistent.
    if ds == DirState.ALREADY_DONE:
        return OnboardResumeState.BROKEN
    if _read_project_yaml(project_path).get("onboard_completed_at"):
        return OnboardResumeState.ALREADY_DONE

    # --- Scanner pass ----------------------------------------------------
    scan_ticket = await tickets.get("onboard-scan")
    if scan_ticket is None:
        return OnboardResumeState.SCAN_PASS
    scan_entries = await threads.for_ticket("onboard-scan")
    scan_done = any(
        isinstance(e, Note) and e.payload.get("kind") == "onboard_scan_done"
        for e in scan_entries
    )
    if not scan_done:
        return OnboardResumeState.SCAN_PASS

    # --- PO read pass + PO review gate ------------------------------------
    brief = await tickets.get("brief")
    if brief is None:
        return OnboardResumeState.PO_READ_PASS
    if await _ticket_awaits_answer(tickets, threads, "brief"):
        return OnboardResumeState.NEEDS_ANSWER_BRIEF

    brief_entries = await threads.for_ticket("brief")
    last_handoff_idx = -1
    last_approved_idx = -1
    last_gaps_idx = -1
    has_spec_gen_event = False
    for i, e in enumerate(brief_entries):
        if isinstance(e, Handoff):
            last_handoff_idx = i
        elif isinstance(e, SystemEvent):
            if e.event_type == "spec_generated":
                has_spec_gen_event = True
            elif e.event_type == "spec_gaps_reported":
                last_gaps_idx = i
            elif e.event_type == "brief_approved":
                last_approved_idx = i

    if last_handoff_idx < 0 and not has_spec_gen_event:
        return OnboardResumeState.PO_READ_PASS
    if not has_spec_gen_event:
        # Approval is fresh only when it came after both the latest PO
        # handoff and the latest gap report. A gap report after the
        # approval routes back to the operator gate (edit brief.md /
        # resume PO / re-approve) rather than re-running the spec
        # generator unattended in a loop.
        if last_approved_idx > last_handoff_idx and last_approved_idx > last_gaps_idx:
            return OnboardResumeState.SPEC_PASS
        return OnboardResumeState.PO_REVIEW

    # --- PM profile pass ---------------------------------------------------
    # ``--profile`` bypass and post-confirm both land here: the signal is
    # ``cfg.profile.name`` non-empty (written by apply_profile/save_config),
    # never a CLI flag — flags are absent on resume.
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
            return OnboardResumeState.PM_PROFILE_PASS
        return OnboardResumeState.PM_PROFILE_CONFIRM_PROMPT

    # --- Phase 2 (blocked on sa-architect Phase 2) --------------------------
    raise NotImplementedError(
        "onboard SA read pass (SA_READ_PASS and later states) is blocked "
        "on sa-architect Phase 2 — the unified SA role interface is not "
        "frozen yet. Phase 1 of jig onboard ends at profile selection."
    )


async def run_onboard(
    *,
    path: Path,
    brief_file: Path | None = None,
    force: bool = False,
    profile_name: str | None = None,
    console: "Console | None" = None,
    prompts: "PromptHandler | None" = None,
) -> None:
    """Top-level onboard flow. Initializes onboard state then drives
    the resume loop.

    Initialization order matters for crash-resume:

    1. Preflight via ``classify_directory`` — reject completed or
       non-onboard projects unless ``--force``.
    2. ``--force``: snapshot operator-authored profile/workflow YAMLs,
       confirm, ``rmtree(.jig/)``.
    3. ``create_stub`` + write ``onboard_started_at`` so
       ``classify_directory`` returns IN_PROGRESS on crash-resume.
    4. Create ``.jig/onboard/``.
    5. Copy ``--brief`` to ``.jig/onboard/desired-state.md`` (the
       PM_BACKLOG gate).
    6. Restore snapshotted profile/workflow YAMLs.
    """
    from jig.init_prompts import CliPromptHandler

    console = console or _spawn_console()
    prompts = prompts or CliPromptHandler()
    target = path

    # 1. Preflight.
    ds = classify_directory(target)
    if not force:
        if ds == DirState.ALREADY_DONE:
            raise click.ClickException(
                f"{target} was initialized by `jig init`. "
                "Use --force to clear .jig/ and onboard from scratch."
            )
        if ds == DirState.BROKEN:
            raise click.ClickException(
                f"{target}/.jig is in an inconsistent state. Use --force to reset."
            )
        if ds == DirState.IN_PROGRESS and not is_onboard_project(target):
            raise click.ClickException(
                f"{target} has an in-progress `jig init`. Finish it or use "
                "--force to clear .jig/ and onboard instead."
            )

    # 2. --force: snapshot operator-authored YAMLs across the rmtree
    # (same blast-radius guard as init_workflow's --force path).
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

    # 3. Stub first — guarantees IN_PROGRESS (not BROKEN) on crash-resume.
    create_stub(target, name=target.name)
    project_yaml = target / ".jig" / "project.yaml"
    pdata = yaml.safe_load(project_yaml.read_text()) or {}
    if not pdata.get("onboard_started_at"):
        pdata["onboard_started_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_text(project_yaml, yaml.safe_dump(pdata, sort_keys=False))

    # 4. Onboard state directory.
    _onboard_dir(target).mkdir(parents=True, exist_ok=True)

    # 5. Desired-state brief (the PM_BACKLOG gate). Inside .jig/onboard/
    # so --force clears it with the rest of the onboard state.
    if brief_file is not None:
        atomic_write_text(
            _onboard_dir(target) / "desired-state.md", brief_file.read_text()
        )

    # 6. Restore the snapshot (no-op without --force or local YAMLs).
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

    # ``--profile`` bypass: apply the named profile directly so the PM
    # profile pass is skipped. Written AFTER the --force cleanup so the
    # rmtree doesn't delete the write; classify_onboard_resume reads
    # ``cfg.profile.name`` on every tick.
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

    from jig.logging_setup import configure_logging

    log_file = configure_logging(target, verbose=False, console=False)
    try:
        log_display = log_file.relative_to(target)
    except ValueError:
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
    from jig.ticket_events import wire_create_publisher

    wire_create_publisher(tickets, bus, sender="onboard")

    try:
        await _run_onboard_resume_loop(
            target=target,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
            prompts=prompts,
            console=console,
        )
    finally:
        await tickets.drain_background_tasks()


async def _run_onboard_resume_loop(
    *,
    target: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    prompts: "PromptHandler",
    console: "Console",
) -> None:
    """Resume-state dispatch loop. Each tick classifies and advances
    one step. Dispatch arms land with their implementation steps."""
    while True:
        rs = await classify_onboard_resume(
            project_path=target, tickets=tickets, threads=threads
        )
        if rs == OnboardResumeState.ALREADY_DONE:
            console.print(
                f"{target} is already onboarded. Next: run `jig start` here.",
                markup=False,
            )
            return
        if rs == OnboardResumeState.BROKEN:
            raise click.ClickException(
                f"{target}/.jig is inconsistent. Use --force to reset."
            )
        raise NotImplementedError(f"onboard dispatch not yet wired for: {rs}")
