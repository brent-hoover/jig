from pathlib import Path

import pytest
import yaml

from jig.config import load_config, save_config
from jig.init_workflow import ResumeState, classify_resume, create_stub
from jig.profile_loader import apply_profile, load_profile
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff, Note, Question, SystemEvent
from jig.ticket import Ticket, TicketStatus, WorkType


def _seed_profile(project_path: Path, name: str = "small") -> None:
    """Pre-apply a profile to skip ``PM_PROFILE_PASS`` for tests that
    target downstream states (BRANCH_PROMPT, SA_*, etc.).

    ``classify_resume`` now intercepts after SPEC_GENERATION to route
    profile selection through PM-1. Tests written before profiles
    existed expect downstream states directly, so they need the
    profile pre-applied to fall through.
    """
    cfg = apply_profile(load_config(project_path), load_profile(name))
    save_config(project_path, cfg)


@pytest.fixture
async def wired(tmp_path: Path):
    create_stub(tmp_path, name="p")
    tickets = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    await tickets.load()
    await threads.load()
    return {"path": tmp_path, "tickets": tickets, "threads": threads}


async def test_resume_fresh(wired):
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.PO_CONVERSATION
    )


async def test_resume_po_conversation_in_progress(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.PO_CONVERSATION
    )


async def test_resume_after_handoff_before_spec(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.BRIEF_APPROVAL
    )


async def test_resume_gap_prompt(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    await wired["threads"].post(
        Note(
            ticket_id="brief",
            author="spec-generator",
            text="gaps",
            payload={
                "gaps": [
                    {
                        "kind": "missing",
                        "severity": "blocking",
                        "location": "x",
                        "description": "d",
                    }
                ]
            },
        )
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_gaps_reported",
            content="",
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.GAP_PROMPT
    )


async def test_resume_branch_prompt(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="",
        )
    )
    _seed_profile(wired["path"])
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.BRANCH_PROMPT
    )


async def test_resume_sa_in_progress(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="",
        )
    )
    await wired["tickets"].create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="a",
            created_by="cli",
        )
    )
    _seed_profile(wired["path"])
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.SA_CONVERSATION
    )


async def test_resume_sa_confirm_prompt(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="",
        )
    )
    await wired["tickets"].create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="a",
            created_by="cli",
        )
    )
    await wired["threads"].post(
        Note(
            ticket_id="architecture",
            author="sa",
            text="propose",
            payload={
                "kind": "sa_propose_scaffold",
                "template_name": "python",
                "rationale": "r",
                "config": {},
            },
        )
    )
    _seed_profile(wired["path"])
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.SA_CONFIRM_PROMPT
    )


async def test_resume_direct_pick_pending(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="",
        )
    )
    await wired["tickets"].create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="a",
            created_by="cli",
        )
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="architecture",
            author="cli",
            event_type="sa_skipped",
            content="",
        )
    )
    _seed_profile(wired["path"])
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.DIRECT_TEMPLATE_PICK
    )


async def test_resume_needs_answer_brief(wired):
    """Brief in needs_info with an open Question routes to NEEDS_ANSWER_BRIEF
    instead of re-spawning PO into a no-op loop."""
    await wired["tickets"].create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
            status=TicketStatus.NEEDS_INFO,
        )
    )
    await wired["threads"].post(
        Question(
            ticket_id="brief",
            author="po",
            target="any_human",
            question="What is it?",
            blocking=True,
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.NEEDS_ANSWER_BRIEF
    )


async def test_resume_brief_answered_falls_through_to_po(wired):
    """An already-resolved Question must not pin the loop on
    NEEDS_ANSWER_BRIEF — PO needs to resume once the user has answered."""
    await wired["tickets"].create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
            status=TicketStatus.IN_PROGRESS,
        )
    )
    await wired["threads"].post(
        Question(
            ticket_id="brief",
            author="po",
            target="any_human",
            question="resolved already",
            blocking=True,
            resolved_by="po",
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.PO_CONVERSATION
    )


async def test_resume_needs_answer_arch(wired):
    """Architecture ticket in needs_info with an open Question routes to
    NEEDS_ANSWER_ARCH instead of re-spawning SA."""
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="",
        )
    )
    await wired["tickets"].create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="a",
            created_by="cli",
            status=TicketStatus.NEEDS_INFO,
        )
    )
    await wired["threads"].post(
        Question(
            ticket_id="architecture",
            author="sa",
            target="any_human",
            question="Which target?",
            blocking=True,
        )
    )
    _seed_profile(wired["path"])
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.NEEDS_ANSWER_ARCH
    )


async def test_reactivate_if_resolved_flips_resolved_to_in_progress(wired):
    """run_*_conversation calls this before respawning so the new
    agent doesn't immediately exit on a still-RESOLVED ticket left
    by the prior round's handoff handler."""
    from jig.init_workflow import _reactivate_if_resolved

    await wired["tickets"].create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
            status=TicketStatus.RESOLVED,
        )
    )
    brief = await wired["tickets"].get("brief")
    assert brief is not None
    out = await _reactivate_if_resolved(wired["tickets"], brief, author="cli")
    assert out.status == TicketStatus.IN_PROGRESS
    persisted = await wired["tickets"].get("brief")
    assert persisted is not None
    assert persisted.status == TicketStatus.IN_PROGRESS


async def test_reactivate_if_resolved_is_noop_for_other_status(wired):
    """Don't disturb a ticket that's IN_PROGRESS / NEEDS_INFO /
    BLOCKED — the helper only flips RESOLVED."""
    from jig.init_workflow import _reactivate_if_resolved

    await wired["tickets"].create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
            status=TicketStatus.IN_PROGRESS,
        )
    )
    brief = await wired["tickets"].get("brief")
    assert brief is not None
    out = await _reactivate_if_resolved(wired["tickets"], brief, author="cli")
    assert out.status == TicketStatus.IN_PROGRESS


async def test_resume_already_done_detected_via_dirstate(wired):
    project_yaml = wired["path"] / ".jig" / "project.yaml"
    data = yaml.safe_load(project_yaml.read_text())
    data["template_applied_at"] = "2026-04-24T00:00:00Z"
    project_yaml.write_text(yaml.safe_dump(data))
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.ALREADY_DONE
    )


async def test_resume_brief_approval_after_handoff_before_spec(wired):
    """After PO posts a Handoff, BRIEF_APPROVAL fires until the operator
    posts a brief_approved SystemEvent."""
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.BRIEF_APPROVAL
    )


async def test_resume_spec_generation_after_brief_approved(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="cli",
            event_type="brief_approved",
            content="approved",
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.SPEC_GENERATION
    )


async def test_resume_brief_approval_again_after_re_handoff(wired):
    """Operator picked [r]esume PO; PO posted a new Handoff; we should
    fire BRIEF_APPROVAL again, not skip to SPEC_GENERATION."""
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    # First round
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="cli",
            event_type="brief_approved",
            content="approved",
        )
    )
    # Second round (operator picked r the first time, PO re-finished)
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.BRIEF_APPROVAL
    )
