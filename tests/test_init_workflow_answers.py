"""Tests for `prompt_and_post_answers` — the CLI-side answer flow that
unblocks PO/SA after they call `ask_question` during `jig init`.

These tests assert the same persistence behavior the TUI's
`ws_server._handle_answer_questions` produces, so the two answer
channels stay observationally identical.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.init_workflow import create_stub, prompt_and_post_answers
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Answer, Question, SystemEvent
from jig.ticket import Ticket, TicketStatus, WorkType


@pytest.fixture
async def wired(tmp_path: Path):
    create_stub(tmp_path, name="p")
    store = tmp_path / ".jig" / "store"
    tickets = TicketStore(store / "tickets.jsonl")
    threads = ThreadStore(store / "comments.jsonl")
    bus = MessageBus(store / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    return {"path": tmp_path, "tickets": tickets, "threads": threads, "bus": bus}


async def _seed_brief_with_questions(
    wired, *, questions: list[str], assignee: str | None = "po"
) -> list[str]:
    await wired["tickets"].create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
            assignee=assignee,
            status=TicketStatus.NEEDS_INFO,
        )
    )
    qids: list[str] = []
    for q in questions:
        qid = await wired["threads"].post(
            Question(
                ticket_id="brief",
                author="po",
                target="any_human",
                question=q,
                blocking=True,
            )
        )
        qids.append(qid)
    return qids


async def test_one_question_one_answer(wired, monkeypatch):
    qids = await _seed_brief_with_questions(wired, questions=["What is it?"])

    answers = iter(["a tool for doing X"])
    monkeypatch.setattr(
        "jig.init_workflow.click.prompt", lambda *a, **kw: next(answers)
    )

    await prompt_and_post_answers(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        ticket_id="brief",
    )

    entries = await wired["threads"].for_ticket("brief")
    answer_entries = [e for e in entries if isinstance(e, Answer)]
    assert len(answer_entries) == 1
    assert answer_entries[0].question_id == qids[0]
    assert answer_entries[0].text == "a tool for doing X"
    assert answer_entries[0].author == "user"

    # status flipped back to in_progress so the next loop iteration
    # re-spawns PO instead of looping on NEEDS_ANSWER_BRIEF.
    ticket = await wired["tickets"].get("brief")
    assert ticket is not None
    assert ticket.status == TicketStatus.IN_PROGRESS

    # status_change SystemEvent is recorded for the audit trail.
    sys_events = [
        e for e in entries
        if isinstance(e, SystemEvent) and e.event_type == "status_change"
    ]
    assert any("needs_info -> in_progress" in e.content for e in sys_events)


async def test_multiple_questions_each_get_an_answer(wired, monkeypatch):
    qids = await _seed_brief_with_questions(
        wired, questions=["who?", "what?", "why?"]
    )

    answers = iter(["users", "thing X", "to solve Y"])
    monkeypatch.setattr(
        "jig.init_workflow.click.prompt", lambda *a, **kw: next(answers)
    )

    await prompt_and_post_answers(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        ticket_id="brief",
    )

    entries = await wired["threads"].for_ticket("brief")
    answer_entries = sorted(
        (e for e in entries if isinstance(e, Answer)),
        key=lambda e: e.created_at,
    )
    assert [a.question_id for a in answer_entries] == qids
    assert [a.text for a in answer_entries] == ["users", "thing X", "to solve Y"]


async def test_publishes_bus_events(wired, monkeypatch):
    await _seed_brief_with_questions(wired, questions=["q1"])

    answers = iter(["a1"])
    monkeypatch.setattr(
        "jig.init_workflow.click.prompt", lambda *a, **kw: next(answers)
    )

    received: list[dict] = []
    queue = await wired["bus"].subscribe("tickets.brief")

    await prompt_and_post_answers(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        ticket_id="brief",
    )

    # Drain everything posted on the brief topic.
    while not queue.empty():
        msg = queue.get_nowait()
        received.append(msg.payload)

    kinds = [p.get("kind") for p in received]
    assert "comment_posted" in kinds
    assert "ticket_updated" in kinds
    updated = next(p for p in received if p.get("kind") == "ticket_updated")
    assert updated["ticket_id"] == "brief"
    assert updated["status"] == "in_progress"


async def test_already_answered_questions_are_not_reprompted(wired, monkeypatch):
    """The init loop respawns PO/SA after each answer round. If a previously-
    answered Question still appeared as 'open', the user would be re-prompted
    for it forever (the asker, not the answerer, marks Questions resolved).
    """
    qids = await _seed_brief_with_questions(wired, questions=["old?", "new?"])
    # User answered the first question in a prior round.
    await wired["threads"].post(
        Answer(
            ticket_id="brief",
            author="user",
            question_id=qids[0],
            text="prior answer",
        )
    )

    answers = iter(["fresh answer"])
    monkeypatch.setattr(
        "jig.init_workflow.click.prompt", lambda *a, **kw: next(answers)
    )

    await prompt_and_post_answers(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        ticket_id="brief",
    )

    entries = await wired["threads"].for_ticket("brief")
    answer_entries = [e for e in entries if isinstance(e, Answer)]
    # Only the new question gets answered this round; the old answer stays.
    assert len(answer_entries) == 2
    new_round = [a for a in answer_entries if a.text == "fresh answer"]
    assert len(new_round) == 1
    assert new_round[0].question_id == qids[1]


async def test_agent_to_agent_questions_are_skipped(wired, monkeypatch):
    """`thread_ask` Questions targeted at a role aren't surfaced to the CLI
    operator — only `target == 'any_human'` questions count.
    """
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
            target="sa",  # agent-to-agent, not for the operator
            question="role-targeted",
            blocking=True,
        )
    )
    await wired["threads"].post(
        Question(
            ticket_id="brief",
            author="po",
            target="any_human",
            question="for the operator",
            blocking=True,
        )
    )

    captured: list[str] = []

    def _record(*args, **kw):
        # click.prompt's first positional arg is the prompt text.
        return "ok"

    monkeypatch.setattr(
        "jig.init_workflow.click.echo", lambda s, *a, **kw: captured.append(s)
    )
    monkeypatch.setattr("jig.init_workflow.click.prompt", _record)

    await prompt_and_post_answers(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        ticket_id="brief",
    )

    entries = await wired["threads"].for_ticket("brief")
    answers = [e for e in entries if isinstance(e, Answer)]
    assert len(answers) == 1
    # The role-targeted question text must not appear in any echoed line.
    joined = "\n".join(captured)
    assert "role-targeted" not in joined
    assert "for the operator" in joined


async def test_label_uses_question_authors_not_assignee(wired, monkeypatch):
    """Brief ticket has no assignee at init time; the prompt header should
    name the asking agent (e.g. 'po'), not fall back to a generic label.
    """
    await wired["tickets"].create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
            assignee=None,
            status=TicketStatus.NEEDS_INFO,
        )
    )
    await wired["threads"].post(
        Question(
            ticket_id="brief",
            author="po",
            target="any_human",
            question="?",
            blocking=True,
        )
    )

    echoes: list[str] = []
    monkeypatch.setattr(
        "jig.init_workflow.click.echo", lambda s, *a, **kw: echoes.append(s)
    )
    monkeypatch.setattr("jig.init_workflow.click.prompt", lambda *a, **kw: "yes")

    await prompt_and_post_answers(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        ticket_id="brief",
    )

    header = next(s for s in echoes if "question(s) on 'brief'" in s)
    assert "po has" in header
    assert "agent has" not in header


async def test_no_open_questions_is_a_noop(wired, monkeypatch):
    """If the ticket has no open Questions, the helper must not prompt
    or flip status — guards against re-entering the answer flow after
    a race or a stale resume classification."""
    await wired["tickets"].create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
            status=TicketStatus.NEEDS_INFO,
        )
    )

    def _fail(*a, **kw):
        raise AssertionError("click.prompt should not be called")

    monkeypatch.setattr("jig.init_workflow.click.prompt", _fail)

    await prompt_and_post_answers(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        ticket_id="brief",
    )

    ticket = await wired["tickets"].get("brief")
    assert ticket is not None
    assert ticket.status == TicketStatus.NEEDS_INFO  # untouched
