from pathlib import Path

import pytest
import yaml

from jig.init_workflow import ResumeState, classify_resume, create_stub
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff, Note, SystemEvent
from jig.ticket import Ticket, WorkType


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
        == ResumeState.SPEC_GENERATION
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
            payload={"gaps": [{"kind": "missing", "severity": "blocking",
                               "location": "x", "description": "d"}]},
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
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.DIRECT_TEMPLATE_PICK
    )


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
