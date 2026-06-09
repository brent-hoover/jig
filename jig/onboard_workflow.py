"""CLI coordinator for ``jig onboard <path>``.

Imports an existing codebase into the jig workflow: a scanner pass
produces ``.jig/onboard/observations.md``, the PO extracts the
current-state brief from existing behavior, the spec generator runs
unchanged, and the PM picks a profile. Phase 2 (SA read pass, operator
review, PM backlog) is blocked on sa-architect Phase 2 — the classifier
returns ``PHASE2_PENDING`` at that boundary and the loop exits cleanly.

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
from typing import TYPE_CHECKING, NamedTuple

import logging

import click
import yaml

if TYPE_CHECKING:
    from rich.console import Console

    from jig.init_prompts import PromptHandler

from jig.atomic import atomic_write_text
from jig.init_workflow import (
    DirState,
    _apply_named_profile,
    _reactivate_if_resolved,
    _run_agent_with_cli_output,
    _spawn_console,
    _ticket_awaits_answer,
    classify_directory,
    create_stub,
)
from jig.persistence import load_role
from jig.project import load_project
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff, Note, SystemEvent
from jig.ticket import Ticket, WorkType

logger = logging.getLogger(__name__)

# Scanner file-ceiling depth budgets by active profile (design
# §"Depth bounding"). SCAN_PASS normally runs before profile selection,
# so the fallback row applies unless the operator passed --profile small
# upfront. Prompt-level (advisory), not transport-enforced.
_SCANNER_FILE_CEILING: dict[str, int] = {"small": 150, "medium": 400}
_SCANNER_FILE_CEILING_FALLBACK = 400


def _active_profile_name(project_path: Path) -> str:
    from jig.config import load_config

    try:
        cfg = load_config(project_path)
    except FileNotFoundError:
        return ""
    return cfg.profile.name.strip()


def scanner_file_ceiling(project_path: Path) -> int:
    """File-ceiling budget for the scanner, scaled by active profile."""
    name = _active_profile_name(project_path)
    return _SCANNER_FILE_CEILING.get(name, _SCANNER_FILE_CEILING_FALLBACK)


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
    # Phase-1 boundary: scan/brief/spec/profile are done; the SA read
    # pass and later states ship with sa-architect Phase 2. A dedicated
    # state (not an exception) so a real bug raising NotImplementedError
    # is never misreported as successful completion.
    PHASE2_PENDING = "phase2_pending"
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


class _BriefThreadScan(NamedTuple):
    """One pass over the brief ticket's thread — the ordering signals
    both ``classify_onboard_resume`` and ``_fresh_gap_note`` key on."""

    last_handoff_idx: int
    last_approved_idx: int
    last_gaps_event_idx: int
    has_spec_gen_event: bool
    last_gap_note: "Note | None"
    last_gap_note_idx: int


async def _scan_brief_thread(threads: ThreadStore) -> _BriefThreadScan:
    entries = await threads.for_ticket("brief")
    last_handoff_idx = -1
    last_approved_idx = -1
    last_gaps_event_idx = -1
    has_spec_gen_event = False
    last_gap_note: Note | None = None
    last_gap_note_idx = -1
    for i, e in enumerate(entries):
        if isinstance(e, Handoff):
            last_handoff_idx = i
        elif isinstance(e, SystemEvent):
            if e.event_type == "spec_generated":
                has_spec_gen_event = True
            elif e.event_type == "spec_gaps_reported":
                last_gaps_event_idx = i
            elif e.event_type == "brief_approved":
                last_approved_idx = i
        elif isinstance(e, Note) and "gaps" in e.payload:
            last_gap_note = e
            last_gap_note_idx = i
    return _BriefThreadScan(
        last_handoff_idx=last_handoff_idx,
        last_approved_idx=last_approved_idx,
        last_gaps_event_idx=last_gaps_event_idx,
        has_spec_gen_event=has_spec_gen_event,
        last_gap_note=last_gap_note,
        last_gap_note_idx=last_gap_note_idx,
    )


async def classify_onboard_resume(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
) -> OnboardResumeState:
    """Pure inspection. Maps persisted onboard state to the next action.

    Implements all transitions up through PM_PROFILE_CONFIRM_PROMPT.
    Past that, ``PHASE2_PENDING`` marks the Phase-1 boundary — the
    SA_READ_PASS → PM_BACKLOG states are unreachable until sa-architect
    Phase 2 freezes the unified SA role interface.
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

    scan = await _scan_brief_thread(threads)
    if scan.last_handoff_idx < 0 and not scan.has_spec_gen_event:
        return OnboardResumeState.PO_READ_PASS
    if not scan.has_spec_gen_event:
        # Approval is fresh only when it came after both the latest PO
        # handoff and the latest gap report. A gap report after the
        # approval routes back to the operator gate (edit brief.md /
        # resume PO / re-approve) rather than re-running the spec
        # generator unattended in a loop.
        if (
            scan.last_approved_idx > scan.last_handoff_idx
            and scan.last_approved_idx > scan.last_gaps_event_idx
        ):
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
    return OnboardResumeState.PHASE2_PENDING


def _observations_text(project_path: Path) -> str:
    """Scanner output. The state machine never reaches the PO pass
    without a scan-done note, and ``onboard_finish_scan`` refuses to
    finish without the file — a missing file here means someone deleted
    it after the scan. Fail loudly rather than spawning a PO with an
    empty observations section."""
    obs = _onboard_dir(project_path) / "observations.md"
    if not obs.is_file():
        raise click.ClickException(
            f"{obs} is missing but the scan was marked complete. "
            "Re-run `jig onboard --force` to re-scan."
        )
    return obs.read_text()


def _po_read_mode_description(project_path: Path) -> str:
    """Brief-ticket description for the onboard PO read pass.

    The PO's tool set is MCP-only (no Read/Glob/Grep), so the scanner's
    observations are embedded here rather than referenced by path.
    """
    observations = _observations_text(project_path)
    return (
        "READ MODE — this project is being onboarded from an EXISTING "
        "codebase.\n\n"
        "Extract the capabilities that already exist from the scanner's "
        "observations below and record them in the brief (existing, "
        "working capabilities belong under '## Built'). Do NOT invent "
        "capabilities that are not yet built. Ask the operator when the "
        "observations leave a capability's behavior unclear.\n\n"
        "## Scanner observations (.jig/onboard/observations.md)\n\n"
        f"{observations}"
    )


async def run_onboard_po_conversation(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    console: "Console | None" = None,
) -> None:
    """Create (if needed) the standard ``brief`` ticket with read-mode
    context injected and spawn the PO agent on it.

    The PO's MCP tools run unchanged (``brief_set_section`` /
    ``po_finish_brief``) so ``run_spec_generator`` works unmodified in
    the next state.
    """
    brief = await tickets.get("brief")
    if brief is None:
        brief = Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="Project brief (onboard read pass)",
            description=_po_read_mode_description(project_path),
            created_by="cli",
        )
        await tickets.create(brief)
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
        role_label="Product Owner (read pass)",
        console=console,
        subtitle="Extracting the current-state brief from the codebase",
    )


def _scanner_profile_recommendation(project_path: Path) -> str:
    """The scanner's ``## Profile recommendation`` section, falling back
    to the whole observations document when the heading is missing (the
    scanner prompt mandates it, but the document is free-form prose and
    not schema-validated)."""
    from jig.markdown_sections import get_section

    obs = _onboard_dir(project_path) / "observations.md"
    if not obs.is_file():
        return ""
    try:
        return get_section(obs, "Profile recommendation")
    except KeyError:
        return obs.read_text()


async def run_onboard_pm_profile_pass(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    console: "Console | None" = None,
) -> None:
    """Create the ``profile`` ticket with the scanner's recommendation
    pre-injected, then delegate the PM spawn to ``run_pm_profile_pass``.

    The PM has no Read/Glob/Grep tools — the recommendation signal must
    arrive in the ticket description.
    """
    from jig.init_workflow import run_pm_profile_pass

    profile_ticket = await tickets.get("profile")
    if profile_ticket is None:
        recommendation = _scanner_profile_recommendation(project_path)
        description = (
            "Pick the project profile for this ONBOARDED existing "
            "codebase. Base your choice on the codebase signals below, "
            "not on the brief's ambitions."
        )
        if recommendation.strip():
            description += (
                f"\n\n## Scanner profile recommendation\n\n{recommendation.strip()}"
            )
        await tickets.create(
            Ticket(
                id="profile",
                work_type=WorkType.PROFILE,
                title="Pick project profile",
                description=description,
                created_by="cli",
            )
        )
    await run_pm_profile_pass(
        project_path=project_path,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        console=console,
    )


async def run_onboard_scan_pass(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    console: "Console | None" = None,
) -> None:
    """Create (if needed) the ``onboard-scan`` ticket and spawn the scanner.

    The depth-budget ceiling from the active profile is injected into
    the ticket description — the scanner has no config access, so the
    signal must arrive pre-injected.
    """
    scan = await tickets.get("onboard-scan")
    if scan is None:
        ceiling = scanner_file_ceiling(project_path)
        scan = Ticket(
            id="onboard-scan",
            work_type=WorkType.ONBOARD_SCAN,
            title="Scan existing codebase",
            description=(
                "Read the existing codebase at the project root and write a "
                "structural observations document to "
                ".jig/onboard/observations.md, then call onboard_finish_scan.\n\n"
                f"Depth budget: read at most {ceiling} files. If you hit the "
                "ceiling before covering the whole tree, stop and add a "
                "'## Depth limit reached' section to observations.md listing "
                "what was not scanned."
            ),
            created_by="cli",
        )
        await tickets.create(scan)
    scan = await _reactivate_if_resolved(tickets, scan, author="cli")
    project = load_project(project_path)
    role_cfg = load_role(project_path, "scanner")
    ctx = AgentSpawnContext(
        role="scanner",
        role_cfg=role_cfg,
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=scan,
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
        role_label="Scanner",
        console=console,
        subtitle="Reading the existing codebase into observations.md",
    )


async def _fresh_gap_note(threads: ThreadStore) -> Note | None:
    """The latest spec-gap Note on the brief ticket, when it postdates
    both the last PO handoff and the last operator approval — i.e. the
    gaps are why the loop is back at PO_REVIEW. ``None`` otherwise."""
    scan = await _scan_brief_thread(threads)
    if (
        scan.last_gap_note_idx > scan.last_handoff_idx
        and scan.last_gap_note_idx > scan.last_approved_idx
    ):
        return scan.last_gap_note
    return None


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
    # Validate --profile before any state mutation — a typo'd name must
    # not leave an initialized onboard stub behind (a bare re-run would
    # then resume and silently drop the intended bypass).
    if profile_name is not None:
        from jig.profile_loader import load_profile

        try:
            load_profile(profile_name, project_path=target)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc)) from exc
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
    # Resolve before taking .name: the CLI default path is "." and
    # ``Path(".").name`` is "" (the greenfield flow has the same guard).
    create_stub(target, name=target.resolve().name)
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
        _apply_named_profile(target, profile_name, console, rerun_hint="`jig onboard`")

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
        if rs == OnboardResumeState.PHASE2_PENDING:
            console.print(
                "Onboard Phase 1 complete: scan, current-state brief, "
                "structured spec, and profile are in place. The SA read "
                "pass, operator review, and backlog bootstrap land with "
                "sa-architect Phase 2 — re-run `jig onboard` once it "
                "ships.",
                markup=False,
            )
            return
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
        if rs == OnboardResumeState.SCAN_PASS:
            await run_onboard_scan_pass(
                project_path=target,
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                console=console,
            )
            continue
        if rs == OnboardResumeState.PO_READ_PASS:
            await run_onboard_po_conversation(
                project_path=target,
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                console=console,
            )
            continue
        if rs == OnboardResumeState.NEEDS_ANSWER_BRIEF:
            from jig.init_workflow import prompt_and_post_answers

            await prompt_and_post_answers(
                tickets=tickets,
                threads=threads,
                bus=bus,
                ticket_id="brief",
                console=console,
                prompts=prompts,
            )
            continue
        if rs == OnboardResumeState.PO_REVIEW:
            from jig.init_workflow import BriefApprovalChoice

            # If the loop is back here because spec generation reported
            # gaps after the last approval, show them — re-approving an
            # unchanged brief would just re-run the generator into the
            # same gaps.
            gap_note = await _fresh_gap_note(threads)
            if gap_note is not None:
                from jig.spec_generator import Gap

                gaps = [Gap.model_validate(g) for g in gap_note.payload["gaps"]]
                lines = ["Spec generation reported gaps in the brief:"]
                lines += [
                    f"  - [{g.severity}] {g.location}: {g.description}" for g in gaps
                ]
                lines.append(
                    "Edit docs/brief.md (or resume the PO) before re-approving."
                )
                console.print("\n".join(lines), markup=False)
            decision = await prompts.ask_brief_approval(
                project_path=target, console=console
            )
            if decision == BriefApprovalChoice.YES:
                await threads.post(
                    SystemEvent(
                        ticket_id="brief",
                        author="cli",
                        event_type="brief_approved",
                        content="operator approved onboard brief",
                    )
                )
                continue
            if decision == BriefApprovalChoice.RESUME:
                # Respawn the PO directly — the stale Handoff still
                # outranks any prior approval, so classification alone
                # would bounce straight back to PO_REVIEW.
                await run_onboard_po_conversation(
                    project_path=target,
                    tickets=tickets,
                    threads=threads,
                    memory=memory,
                    bus=bus,
                    console=console,
                )
                continue
            console.print("Brief not approved. State saved.", markup=False)
            return
        if rs == OnboardResumeState.SPEC_PASS:
            from jig.init_workflow import _cli_emitter
            from jig.spec_generator import run_spec_generator

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
        if rs == OnboardResumeState.PM_PROFILE_PASS:
            await run_onboard_pm_profile_pass(
                project_path=target,
                tickets=tickets,
                threads=threads,
                memory=memory,
                bus=bus,
                console=console,
            )
            continue
        if rs == OnboardResumeState.PM_PROFILE_CONFIRM_PROMPT:
            from jig.init_workflow import ConfirmChoice, prompt_profile_confirm

            decision, proposal = await prompt_profile_confirm(
                threads, console=console, prompts=prompts
            )
            if decision == ConfirmChoice.NO:
                console.print("Profile not approved. State saved.", markup=False)
                return
            # YES accepts the PM's choice; SWAP flips to the other
            # shipped profile (same hard-wired toggle as the greenfield
            # flow — handle_pm_propose_profile restricts PM-1 to
            # small/medium).
            chosen_name = proposal["name"]
            if decision == ConfirmChoice.SWAP:
                if chosen_name not in {"small", "medium"}:
                    raise click.ClickException(
                        f"SWAP requires a shipped profile name; got "
                        f"{chosen_name!r}. handle_pm_propose_profile "
                        "must restrict PM-1 to small/medium."
                    )
                chosen_name = "small" if chosen_name == "medium" else "medium"
            _apply_named_profile(
                target, chosen_name, console, rerun_hint="`jig onboard`"
            )
            continue
        raise NotImplementedError(f"onboard dispatch not yet wired for: {rs}")
