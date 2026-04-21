"""Tests for the thread_mcp handlers (Phase 4 Tasks C + D).

Task C (Q&A) covers:

* ``thread_ask`` creates a typed Question, respects ``blocking``, and
  publishes to the bus so other subscribers see it.
* ``thread_answer`` creates an Answer pointing at a Question and fails
  loud if the question is already resolved or the target isn't a
  Question.
* ``thread_resolve_question`` refuses non-asker closes (doc 08 asymmetry)
  and optionally records an accepted answer.

Task D (Objection / Resolution / Waiver) covers:

* ``thread_object`` creates a blocking Objection.
* ``thread_resolve_objection`` posts a Resolution but does NOT close the
  Objection (asymmetry: only the objector closes).
* ``thread_accept_resolution`` is objector-only.
* ``thread_waive`` enforces ``config.waiver_authority`` and both the
  Waiver and the Objection stay in the thread for audit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.config import Config, save_config
from jig.models import PhaseConfig, SpecificRoleEvaluator, WorkflowConfig
from jig.persistence import save_workflow
from jig.project import Project
from jig.store import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import (
    Answer,
    Decision,
    Escalation,
    Handoff,
    Note,
    Objection,
    Question,
    Resolution,
    SystemEvent,
    Uncertain,
    Waiver,
)
from jig.thread_mcp import (
    ThreadError,
    handle_thread_accept_handoff,
    handle_thread_accept_resolution,
    handle_thread_answer,
    handle_thread_ask,
    handle_thread_decide,
    handle_thread_escalate,
    handle_thread_handoff,
    handle_thread_note,
    handle_thread_object,
    handle_thread_reject_handoff,
    handle_thread_resolve_objection,
    handle_thread_resolve_question,
    handle_thread_uncertain,
    handle_thread_waive,
    handle_thread_waive_check,
)
from jig.ticket import Ticket, WorkType


# ---- fixtures -------------------------------------------------------------


async def _make_stores(tmp_path: Path) -> tuple[
    TicketStore, ThreadStore, MessageBus, str
]:
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    ticket_id = await tickets.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="orchestrator",
        )
    )
    return tickets, threads, bus, ticket_id


# ---- thread_ask -----------------------------------------------------------


class TestThreadAsk:
    @pytest.mark.asyncio
    async def test_creates_question_and_returns_id(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "should we cache this?",
            },
        )
        qid = result["question_id"]
        assert result["blocking"] is False
        assert result["target"] == "reviewer"
        entry = await threads.get(qid)
        assert isinstance(entry, Question)
        assert entry.question == "should we cache this?"
        assert entry.target == "reviewer"
        assert entry.author == "dev"
        assert entry.blocking is False
        assert not entry.is_blocking()

    @pytest.mark.asyncio
    async def test_blocking_question_gates(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "need sign-off?",
                "blocking": True,
            },
        )
        blockers = await threads.has_unresolved_blocking(ticket_id)
        assert len(blockers) == 1

    @pytest.mark.asyncio
    async def test_publishes_to_bus(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "?",
            },
        )
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        assert any(
            m.payload.get("kind") == "thread_question_posted" for m in msgs
        )

    @pytest.mark.asyncio
    async def test_missing_ticket_raises(self, tmp_path: Path) -> None:
        tickets, threads, bus, _ = await _make_stores(tmp_path)
        with pytest.raises(KeyError, match="ticket"):
            await handle_thread_ask(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "ticket_id": "missing",
                    "target": "reviewer",
                    "question": "?",
                },
            )

    @pytest.mark.asyncio
    async def test_empty_target_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(ValueError, match="target"):
            await handle_thread_ask(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "ticket_id": ticket_id,
                    "target": "  ",
                    "question": "?",
                },
            )


# ---- thread_answer --------------------------------------------------------


class TestThreadAnswer:
    @pytest.mark.asyncio
    async def test_posts_answer_pointing_at_question(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        ask = await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "cache?",
            },
        )
        result = await handle_thread_answer(
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"question_id": ask["question_id"], "text": "yes"},
        )
        answer = await threads.get(result["answer_id"])
        assert isinstance(answer, Answer)
        assert answer.question_id == ask["question_id"]
        assert answer.text == "yes"
        assert answer.author == "reviewer"
        # Answering does NOT resolve the question.
        q = await threads.get(ask["question_id"])
        assert isinstance(q, Question)
        assert not q.is_resolved()

    @pytest.mark.asyncio
    async def test_answer_fails_when_question_resolved(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        ask = await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "cache?",
            },
        )
        await handle_thread_resolve_question(
            threads=threads,
            bus=bus,
            sender="dev",
            args={"question_id": ask["question_id"]},
        )
        with pytest.raises(ThreadError, match="already resolved"):
            await handle_thread_answer(
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={
                    "question_id": ask["question_id"],
                    "text": "too late",
                },
            )

    @pytest.mark.asyncio
    async def test_answer_rejects_non_question_target(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        # Post a note and try to "answer" it.
        from jig.thread import Note

        nid = await threads.post(
            Note(ticket_id=ticket_id, author="x", text="hi")
        )
        with pytest.raises(ThreadError, match="not a question"):
            await handle_thread_answer(
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={"question_id": nid, "text": "huh"},
            )

    @pytest.mark.asyncio
    async def test_answer_missing_question_raises_keyerror(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, _ = await _make_stores(tmp_path)
        with pytest.raises(KeyError):
            await handle_thread_answer(
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={"question_id": "missing", "text": "x"},
            )


# ---- thread_resolve_question ----------------------------------------------


class TestThreadResolveQuestion:
    @pytest.mark.asyncio
    async def test_asker_closes_question(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        ask = await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "?",
                "blocking": True,
            },
        )
        await handle_thread_resolve_question(
            threads=threads,
            bus=bus,
            sender="dev",
            args={"question_id": ask["question_id"]},
        )
        q = await threads.get(ask["question_id"])
        assert isinstance(q, Question)
        assert q.is_resolved()
        assert q.resolved_by == "dev"
        assert await threads.has_unresolved_blocking(ticket_id) == []

    @pytest.mark.asyncio
    async def test_non_asker_refused(self, tmp_path: Path) -> None:
        """doc 08 §Gating semantics: the asker, not the answerer,
        closes. The reviewer cannot resolve a question they received."""
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        ask = await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "?",
            },
        )
        with pytest.raises(ThreadError, match="only the asker"):
            await handle_thread_resolve_question(
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={"question_id": ask["question_id"]},
            )
        q = await threads.get(ask["question_id"])
        assert isinstance(q, Question)
        assert not q.is_resolved()

    @pytest.mark.asyncio
    async def test_double_close_refused(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        ask = await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "?",
            },
        )
        await handle_thread_resolve_question(
            threads=threads,
            bus=bus,
            sender="dev",
            args={"question_id": ask["question_id"]},
        )
        with pytest.raises(ThreadError, match="already resolved"):
            await handle_thread_resolve_question(
                threads=threads,
                bus=bus,
                sender="dev",
                args={"question_id": ask["question_id"]},
            )

    @pytest.mark.asyncio
    async def test_accepted_answer_recorded(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        ask = await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "?",
            },
        )
        ans = await handle_thread_answer(
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"question_id": ask["question_id"], "text": "yes"},
        )
        await handle_thread_resolve_question(
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "question_id": ask["question_id"],
                "accepted_answer_id": ans["answer_id"],
            },
        )
        q = await threads.get(ask["question_id"])
        assert isinstance(q, Question)
        assert q.accepted_answer_id == ans["answer_id"]

    @pytest.mark.asyncio
    async def test_accepted_answer_must_match_question(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        ask_a = await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "A?",
            },
        )
        ask_b = await handle_thread_ask(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "target": "reviewer",
                "question": "B?",
            },
        )
        ans_b = await handle_thread_answer(
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"question_id": ask_b["question_id"], "text": "x"},
        )
        with pytest.raises(ThreadError, match="not against"):
            await handle_thread_resolve_question(
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "question_id": ask_a["question_id"],
                    "accepted_answer_id": ans_b["answer_id"],
                },
            )


# ---- Task D fixtures ------------------------------------------------------


def _write_config(
    tmp_path: Path, *, waiver_authority: list[str] | None = None
) -> None:
    """Persist a minimal `.jig/config.yaml` so `handle_thread_waive`
    can read ``config.waiver_authority``.
    """
    project = Project(
        id="test-project",
        name="test",
        path=str(tmp_path),
    )
    if waiver_authority is None:
        cfg = Config(project=project)  # use default ["po", "sa", "user"]
    else:
        cfg = Config(project=project, waiver_authority=waiver_authority)
    save_config(tmp_path, cfg)


# ---- thread_object --------------------------------------------------------


class TestThreadObject:
    @pytest.mark.asyncio
    async def test_creates_blocking_objection(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "this bypasses CSRF",
            },
        )
        oid = result["objection_id"]
        assert result["blocking"] is True
        entry = await threads.get(oid)
        assert isinstance(entry, Objection)
        assert entry.target_artifact == "src/auth.py"
        assert entry.text == "this bypasses CSRF"
        assert entry.author == "reviewer"
        assert entry.is_blocking()
        # Ticket-level gating sees it.
        blockers = await threads.has_unresolved_blocking(ticket_id)
        assert len(blockers) == 1

    @pytest.mark.asyncio
    async def test_publishes_to_bus(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "nope",
            },
        )
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        assert any(
            m.payload.get("kind") == "thread_objection_posted" for m in msgs
        )

    @pytest.mark.asyncio
    async def test_missing_ticket_raises(self, tmp_path: Path) -> None:
        tickets, threads, bus, _ = await _make_stores(tmp_path)
        with pytest.raises(KeyError, match="ticket"):
            await handle_thread_object(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={
                    "ticket_id": "missing",
                    "target_artifact": "x",
                    "text": "y",
                },
            )

    @pytest.mark.asyncio
    async def test_empty_artifact_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(ValueError, match="target_artifact"):
            await handle_thread_object(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={
                    "ticket_id": ticket_id,
                    "target_artifact": "  ",
                    "text": "nope",
                },
            )

    @pytest.mark.asyncio
    async def test_empty_text_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(ValueError, match="text"):
            await handle_thread_object(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={
                    "ticket_id": ticket_id,
                    "target_artifact": "src/auth.py",
                    "text": "",
                },
            )


# ---- thread_resolve_objection ---------------------------------------------


class TestThreadResolveObjection:
    @pytest.mark.asyncio
    async def test_posts_resolution_without_closing(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        result = await handle_thread_resolve_objection(
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "objection_id": obj["objection_id"],
                "text": "added csrf token check",
            },
        )
        resolution = await threads.get(result["resolution_id"])
        assert isinstance(resolution, Resolution)
        assert resolution.objection_id == obj["objection_id"]
        assert resolution.author == "dev"
        # Objection remains blocking.
        o = await threads.get(obj["objection_id"])
        assert isinstance(o, Objection)
        assert not o.is_resolved()
        assert o.is_blocking()

    @pytest.mark.asyncio
    async def test_fails_when_already_resolved(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        await handle_thread_resolve_objection(
            threads=threads,
            bus=bus,
            sender="dev",
            args={"objection_id": obj["objection_id"], "text": "fixed"},
        )
        await handle_thread_accept_resolution(
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"objection_id": obj["objection_id"]},
        )
        with pytest.raises(ThreadError, match="already resolved"):
            await handle_thread_resolve_objection(
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "objection_id": obj["objection_id"],
                    "text": "another",
                },
            )

    @pytest.mark.asyncio
    async def test_fails_when_waived(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        await handle_thread_waive(
            threads=threads,
            bus=bus,
            sender="po",
            args={
                "objection_id": obj["objection_id"],
                "justification": "shipping for demo",
            },
            project_path=tmp_path,
        )
        with pytest.raises(ThreadError, match="already resolved"):
            await handle_thread_resolve_objection(
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "objection_id": obj["objection_id"],
                    "text": "fixed now",
                },
            )

    @pytest.mark.asyncio
    async def test_rejects_non_objection_target(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        from jig.thread import Note

        nid = await threads.post(
            Note(ticket_id=ticket_id, author="x", text="hi")
        )
        with pytest.raises(ThreadError, match="not an objection"):
            await handle_thread_resolve_objection(
                threads=threads,
                bus=bus,
                sender="dev",
                args={"objection_id": nid, "text": "fixed"},
            )


# ---- thread_accept_resolution ---------------------------------------------


class TestThreadAcceptResolution:
    @pytest.mark.asyncio
    async def test_objector_closes(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        await handle_thread_resolve_objection(
            threads=threads,
            bus=bus,
            sender="dev",
            args={"objection_id": obj["objection_id"], "text": "fixed"},
        )
        await handle_thread_accept_resolution(
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"objection_id": obj["objection_id"]},
        )
        o = await threads.get(obj["objection_id"])
        assert isinstance(o, Objection)
        assert o.is_resolved()
        assert o.resolved_by == "reviewer"
        assert await threads.has_unresolved_blocking(ticket_id) == []

    @pytest.mark.asyncio
    async def test_non_objector_refused(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        with pytest.raises(ThreadError, match="only the objector"):
            await handle_thread_accept_resolution(
                threads=threads,
                bus=bus,
                sender="dev",
                args={"objection_id": obj["objection_id"]},
            )
        o = await threads.get(obj["objection_id"])
        assert isinstance(o, Objection)
        assert not o.is_resolved()

    @pytest.mark.asyncio
    async def test_double_close_refused(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        await handle_thread_accept_resolution(
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"objection_id": obj["objection_id"]},
        )
        with pytest.raises(ThreadError, match="already resolved"):
            await handle_thread_accept_resolution(
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={"objection_id": obj["objection_id"]},
            )


# ---- thread_waive ---------------------------------------------------------


class TestThreadWaive:
    @pytest.mark.asyncio
    async def test_authorized_role_waives(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)  # default authority = ["po", "sa", "user"]
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        result = await handle_thread_waive(
            threads=threads,
            bus=bus,
            sender="po",
            args={
                "objection_id": obj["objection_id"],
                "justification": "shipping for demo, ticket tracks real fix",
            },
            project_path=tmp_path,
        )
        # Waiver entry is in the thread.
        waiver = await threads.get(result["waiver_id"])
        assert isinstance(waiver, Waiver)
        assert waiver.objection_id == obj["objection_id"]
        assert waiver.author == "po"
        # Objection is flipped to waived and no longer blocking.
        o = await threads.get(obj["objection_id"])
        assert isinstance(o, Objection)
        assert o.waived_by == "po"
        assert o.is_resolved()
        assert not o.is_blocking()
        assert await threads.has_unresolved_blocking(ticket_id) == []

    @pytest.mark.asyncio
    async def test_unauthorized_role_refused(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path, waiver_authority=["po", "sa"])
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        with pytest.raises(ThreadError, match="not authorized"):
            await handle_thread_waive(
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "objection_id": obj["objection_id"],
                    "justification": "I promise it's fine",
                },
                project_path=tmp_path,
            )
        # Objection untouched — still blocking.
        o = await threads.get(obj["objection_id"])
        assert isinstance(o, Objection)
        assert o.waived_by is None
        assert o.is_blocking()

    @pytest.mark.asyncio
    async def test_empty_justification_rejected(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        with pytest.raises(ValueError, match="justification"):
            await handle_thread_waive(
                threads=threads,
                bus=bus,
                sender="po",
                args={
                    "objection_id": obj["objection_id"],
                    "justification": "  ",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_fails_on_already_resolved(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        await handle_thread_accept_resolution(
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"objection_id": obj["objection_id"]},
        )
        with pytest.raises(ThreadError, match="already resolved"):
            await handle_thread_waive(
                threads=threads,
                bus=bus,
                sender="po",
                args={
                    "objection_id": obj["objection_id"],
                    "justification": "too late",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_publishes_to_bus(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        obj = await handle_thread_object(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "ticket_id": ticket_id,
                "target_artifact": "src/auth.py",
                "text": "CSRF",
            },
        )
        await handle_thread_waive(
            threads=threads,
            bus=bus,
            sender="po",
            args={
                "objection_id": obj["objection_id"],
                "justification": "shipping",
            },
            project_path=tmp_path,
        )
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        assert any(
            m.payload.get("kind") == "thread_objection_waived" for m in msgs
        )


# ---- thread_decide --------------------------------------------------------


class TestThreadDecide:
    @pytest.mark.asyncio
    async def test_creates_decision_and_record_file(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_decide(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="sa",
            args={
                "ticket_id": ticket_id,
                "decision": "use JSONL, not sqlite",
                "rationale": "append-only writes + zero-dep deploy story",
            },
            project_path=tmp_path,
        )
        did = result["decision_id"]
        assert result["seq"] == 1
        # Thread entry present and auto-resolved.
        entry = await threads.get(did)
        assert isinstance(entry, Decision)
        assert entry.decision == "use JSONL, not sqlite"
        assert entry.is_resolved()
        # Decision record mirror exists with both sections.
        record = tmp_path / ".jig" / "decisions" / f"{ticket_id}-1.md"
        assert record.is_file()
        body = record.read_text()
        assert "## Decision" in body
        assert "use JSONL, not sqlite" in body
        assert "## Rationale" in body
        assert "append-only writes" in body

    @pytest.mark.asyncio
    async def test_seq_increments_per_ticket(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        first = await handle_thread_decide(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="sa",
            args={
                "ticket_id": ticket_id,
                "decision": "A",
                "rationale": "because",
            },
            project_path=tmp_path,
        )
        second = await handle_thread_decide(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="sa",
            args={
                "ticket_id": ticket_id,
                "decision": "B",
                "rationale": "also because",
            },
            project_path=tmp_path,
        )
        assert first["seq"] == 1
        assert second["seq"] == 2
        assert (tmp_path / ".jig" / "decisions" / f"{ticket_id}-2.md").is_file()

    @pytest.mark.asyncio
    async def test_publishes_to_bus(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        await handle_thread_decide(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="sa",
            args={
                "ticket_id": ticket_id,
                "decision": "x",
                "rationale": "y",
            },
            project_path=tmp_path,
        )
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        assert any(
            m.payload.get("kind") == "thread_decision_posted" for m in msgs
        )

    @pytest.mark.asyncio
    async def test_missing_ticket_raises(self, tmp_path: Path) -> None:
        tickets, threads, bus, _ = await _make_stores(tmp_path)
        with pytest.raises(KeyError, match="ticket"):
            await handle_thread_decide(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="sa",
                args={
                    "ticket_id": "missing",
                    "decision": "x",
                    "rationale": "y",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_empty_rationale_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(ValueError, match="rationale"):
            await handle_thread_decide(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="sa",
                args={
                    "ticket_id": ticket_id,
                    "decision": "x",
                    "rationale": " ",
                },
                project_path=tmp_path,
            )


# ---- thread_note ----------------------------------------------------------


class TestThreadNote:
    @pytest.mark.asyncio
    async def test_creates_resolved_non_blocking_note(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_note(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={"ticket_id": ticket_id, "text": "investigating flake"},
        )
        note = await threads.get(result["note_id"])
        assert isinstance(note, Note)
        assert note.text == "investigating flake"
        assert note.is_resolved()
        assert not note.is_blocking()
        assert await threads.has_unresolved_blocking(ticket_id) == []

    @pytest.mark.asyncio
    async def test_empty_text_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(ValueError, match="text"):
            await handle_thread_note(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={"ticket_id": ticket_id, "text": ""},
            )

    @pytest.mark.asyncio
    async def test_missing_ticket_raises(self, tmp_path: Path) -> None:
        tickets, threads, bus, _ = await _make_stores(tmp_path)
        with pytest.raises(KeyError, match="ticket"):
            await handle_thread_note(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={"ticket_id": "missing", "text": "x"},
            )


# ---- thread_escalate ------------------------------------------------------


class TestThreadEscalate:
    @pytest.mark.asyncio
    async def test_creates_blocking_escalation(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_escalate(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "reason": "needs_human_judgment",
                "details": "pricing policy ambiguous — legal sign-off?",
            },
        )
        assert result["blocking"] is True
        assert result["target"] == "human"
        entry = await threads.get(result["escalation_id"])
        assert isinstance(entry, Escalation)
        assert entry.target == "human"
        assert entry.is_blocking()
        blockers = await threads.has_unresolved_blocking(ticket_id)
        assert len(blockers) == 1

    @pytest.mark.asyncio
    async def test_custom_target_allowed(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_escalate(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "reason": "architecture_decision",
                "details": "need SA input on transport choice",
                "target": "sa",
            },
            valid_roles=frozenset({"dev", "sa", "reviewer"}),
        )
        entry = await threads.get(result["escalation_id"])
        assert isinstance(entry, Escalation)
        assert entry.target == "sa"

    @pytest.mark.asyncio
    async def test_unknown_target_warns_but_allows(
        self, tmp_path: Path, caplog
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        import logging

        with caplog.at_level(logging.WARNING, logger="jig.thread_mcp"):
            await handle_thread_escalate(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "ticket_id": ticket_id,
                    "reason": "x",
                    "details": "y",
                    "target": "unknown_role",
                },
                valid_roles=frozenset({"dev", "sa"}),
            )
        assert any(
            "not a known role" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_phase_escalation_targets_warn_only(
        self, tmp_path: Path, caplog
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        import logging

        with caplog.at_level(logging.WARNING, logger="jig.thread_mcp"):
            result = await handle_thread_escalate(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "ticket_id": ticket_id,
                    "reason": "x",
                    "details": "y",
                    "target": "po",
                },
                phase_escalation_targets=frozenset({"sa", "human"}),
            )
        # Allowed despite warning — Phase 4 is advisory.
        entry = await threads.get(result["escalation_id"])
        assert isinstance(entry, Escalation)
        assert any(
            "phase escalation_targets" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_empty_reason_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(ValueError, match="reason"):
            await handle_thread_escalate(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "ticket_id": ticket_id,
                    "reason": "",
                    "details": "y",
                },
            )


# ---- thread_uncertain -----------------------------------------------------


class TestThreadUncertain:
    @pytest.mark.asyncio
    async def test_routes_to_question_when_role_mentioned(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_uncertain(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "details": "ask the SA whether we should ship this behind a flag",
            },
            valid_roles=frozenset({"dev", "sa", "reviewer"}),
        )
        assert result["routed"] == "question"
        assert result["target"] == "sa"
        u = await threads.get(result["uncertain_id"])
        assert isinstance(u, Uncertain)
        q = await threads.get(result["question_id"])
        assert isinstance(q, Question)
        assert q.target == "sa"
        assert q.blocking is False

    @pytest.mark.asyncio
    async def test_escalates_when_no_role_matched(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_uncertain(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "details": "not sure who owns pricing policy",
            },
            valid_roles=frozenset({"dev", "sa", "reviewer"}),
        )
        assert result["routed"] == "escalation"
        assert result["target"] == "human"
        esc = await threads.get(result["escalation_id"])
        assert isinstance(esc, Escalation)
        assert esc.reason == "uncertain_unroutable"
        assert esc.target == "human"
        blockers = await threads.has_unresolved_blocking(ticket_id)
        assert len(blockers) == 1  # the escalation is blocking

    @pytest.mark.asyncio
    async def test_word_boundary_prevents_substring_collision(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        # "sa" as substring of "saffron" must NOT route to sa.
        result = await handle_thread_uncertain(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "details": "saffron pricing is weird",
            },
            valid_roles=frozenset({"sa", "dev"}),
        )
        assert result["routed"] == "escalation"

    @pytest.mark.asyncio
    async def test_empty_details_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(ValueError, match="details"):
            await handle_thread_uncertain(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={"ticket_id": ticket_id, "details": "  "},
                valid_roles=frozenset({"sa"}),
            )

    @pytest.mark.asyncio
    async def test_publishes_routed_event(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        await handle_thread_uncertain(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "details": "ask sa about caching",
            },
            valid_roles=frozenset({"sa"}),
        )
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        assert any(
            m.payload.get("kind") == "thread_uncertain_routed_question"
            for m in msgs
        )


# ---- Task F helpers -------------------------------------------------------


def _write_workflow(
    tmp_path: Path, *, name: str = "default", phases: list[PhaseConfig]
) -> None:
    """Persist a workflow under `.jig/workflows/<name>.yaml` so the
    handoff handlers can resolve the evaluator for a phase.
    """
    save_workflow(tmp_path, WorkflowConfig(name=name, phases=phases))


# ---- thread_handoff -------------------------------------------------------


class TestThreadHandoff:
    @pytest.mark.asyncio
    async def test_creates_blocking_pending_handoff(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": ["src/foo.py", "src/bar.py"],
                "summary": "added CRUD endpoints",
            },
        )
        assert result["acceptance_state"] == "pending"
        assert result["blocking"] is True
        entry = await threads.get(result["handoff_id"])
        assert isinstance(entry, Handoff)
        assert entry.phase == "implement"
        assert entry.outputs == ["src/foo.py", "src/bar.py"]
        assert entry.is_blocking()
        assert not entry.is_resolved()
        blockers = await threads.has_unresolved_blocking(ticket_id)
        assert len(blockers) == 1

    @pytest.mark.asyncio
    async def test_deferred_items_coerce_strings(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
                "deferred_items": ["migrate legacy logs", "drop v1 API"],
            },
        )
        entry = await threads.get(result["handoff_id"])
        assert isinstance(entry, Handoff)
        assert [d.item for d in entry.deferred_items] == [
            "migrate legacy logs",
            "drop v1 API",
        ]
        assert all(d.status == "open" for d in entry.deferred_items)

    @pytest.mark.asyncio
    async def test_deferred_items_accept_dict(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        result = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
                "deferred_items": [
                    {"item": "refactor auth", "reason": "out of scope"},
                ],
            },
        )
        entry = await threads.get(result["handoff_id"])
        assert isinstance(entry, Handoff)
        assert entry.deferred_items[0].item == "refactor auth"
        assert entry.deferred_items[0].reason == "out of scope"

    @pytest.mark.asyncio
    async def test_publishes_to_bus(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": ["x"],
            },
        )
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        assert any(
            m.payload.get("kind") == "thread_handoff_posted" for m in msgs
        )

    @pytest.mark.asyncio
    async def test_missing_ticket_raises(self, tmp_path: Path) -> None:
        tickets, threads, bus, _ = await _make_stores(tmp_path)
        with pytest.raises(KeyError, match="ticket"):
            await handle_thread_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "ticket_id": "missing",
                    "phase": "implement",
                    "outputs": [],
                },
            )

    @pytest.mark.asyncio
    async def test_empty_phase_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(ValueError, match="phase"):
            await handle_thread_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "ticket_id": ticket_id,
                    "phase": "",
                    "outputs": [],
                },
            )


# ---- thread_accept_handoff / thread_reject_handoff ------------------------


class TestThreadCloseHandoff:
    @pytest.mark.asyncio
    async def test_evaluator_accepts(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(name="implement", role="dev"),
                PhaseConfig(name="review", role="reviewer"),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        # Next-phase role ("reviewer") is the implicit evaluator.
        result = await handle_thread_accept_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"handoff_id": handoff["handoff_id"]},
            project_path=tmp_path,
        )
        assert result["acceptance_state"] == "accepted"
        h = await threads.get(handoff["handoff_id"])
        assert isinstance(h, Handoff)
        assert h.acceptance_state == "accepted"
        assert h.accepted_by == "reviewer"
        assert h.is_resolved()
        assert await threads.has_unresolved_blocking(ticket_id) == []
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        assert any(
            m.payload.get("kind") == "thread_handoff_accepted" for m in msgs
        )

    @pytest.mark.asyncio
    async def test_explicit_evaluator_field_wins_over_next_phase(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(
                    name="implement",
                    role="dev",
                    evaluator=SpecificRoleEvaluator(
                        type="specific_role", role="sa"
                    ),  # explicit wins
                ),
                PhaseConfig(name="review", role="reviewer"),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        # reviewer is the next phase's role but SA is the explicit
        # evaluator — reviewer must be refused.
        with pytest.raises(ThreadError, match="phase evaluator"):
            await handle_thread_accept_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )
        await handle_thread_accept_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="sa",
            args={"handoff_id": handoff["handoff_id"]},
            project_path=tmp_path,
        )
        h = await threads.get(handoff["handoff_id"])
        assert isinstance(h, Handoff)
        assert h.accepted_by == "sa"

    @pytest.mark.asyncio
    async def test_non_evaluator_refused(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(name="implement", role="dev"),
                PhaseConfig(name="review", role="reviewer"),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        with pytest.raises(ThreadError, match="phase evaluator"):
            await handle_thread_accept_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",  # author can't self-accept
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )
        h = await threads.get(handoff["handoff_id"])
        assert isinstance(h, Handoff)
        assert h.acceptance_state == "pending"

    @pytest.mark.asyncio
    async def test_missing_workflow_raises(
        self, tmp_path: Path
    ) -> None:
        """C1: silently allowing accept/reject when the workflow is
        missing bypasses the doc-10 evaluator-identity guard entirely —
        the handler now fails loud instead."""
        tickets = TicketStore(tmp_path / "tickets.jsonl")
        await tickets.load()
        threads = ThreadStore(tmp_path / "comments.jsonl")
        await threads.load()
        bus = MessageBus(tmp_path / "messages.jsonl")
        await bus.load()
        # Ticket references a workflow that doesn't exist on-disk or in
        # shipped defaults — evaluator identity is undecidable.
        ticket_id = await tickets.create(
            Ticket(
                work_type=WorkType.FEATURE,
                title="t",
                created_by="orchestrator",
                workflow="nonexistent_workflow_4f",
            )
        )
        handoff = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        with pytest.raises(ThreadError, match="workflow"):
            await handle_thread_accept_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="whoever",
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )
        # Handoff must remain pending — no state mutation on the raise.
        h = await threads.get(handoff["handoff_id"])
        assert isinstance(h, Handoff)
        assert h.acceptance_state == "pending"

    @pytest.mark.asyncio
    async def test_terminal_phase_has_no_evaluator_raises(
        self, tmp_path: Path
    ) -> None:
        """C1: a terminal phase (no next phase, no explicit evaluator
        config) has no one to accept the handoff. The handler refuses
        rather than silently letting the first caller win."""
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[PhaseConfig(name="implement", role="dev")],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        with pytest.raises(
            ThreadError, match="cannot determine evaluator"
        ):
            await handle_thread_accept_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )
        h = await threads.get(handoff["handoff_id"])
        assert isinstance(h, Handoff)
        assert h.acceptance_state == "pending"

    @pytest.mark.asyncio
    async def test_reject_publishes_with_reason(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(name="implement", role="dev"),
                PhaseConfig(name="review", role="reviewer"),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        result = await handle_thread_reject_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "handoff_id": handoff["handoff_id"],
                "reason": "missing tests",
            },
            project_path=tmp_path,
        )
        assert result["acceptance_state"] == "rejected"
        h = await threads.get(handoff["handoff_id"])
        assert isinstance(h, Handoff)
        assert h.rejection_reason == "missing tests"
        assert h.is_resolved()
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        reject_msgs = [
            m
            for m in msgs
            if m.payload.get("kind") == "thread_handoff_rejected"
        ]
        assert len(reject_msgs) == 1
        assert reject_msgs[0].payload["rejection_reason"] == "missing tests"
        assert reject_msgs[0].payload["rejected_by"] == "reviewer"

    @pytest.mark.asyncio
    async def test_reject_empty_reason_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(name="implement", role="dev"),
                PhaseConfig(name="review", role="reviewer"),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        with pytest.raises(ValueError, match="reason"):
            await handle_thread_reject_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={
                    "handoff_id": handoff["handoff_id"],
                    "reason": " ",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_double_close_refused(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(name="implement", role="dev"),
                PhaseConfig(name="review", role="reviewer"),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        await handle_thread_accept_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={"handoff_id": handoff["handoff_id"]},
            project_path=tmp_path,
        )
        with pytest.raises(ThreadError, match="already"):
            await handle_thread_accept_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )
        with pytest.raises(ThreadError, match="already"):
            await handle_thread_reject_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="reviewer",
                args={
                    "handoff_id": handoff["handoff_id"],
                    "reason": "too late",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_rejects_non_handoff_target(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[PhaseConfig(name="implement", role="dev")],
        )
        nid = await threads.post(
            Note(ticket_id=ticket_id, author="x", text="hi")
        )
        with pytest.raises(ThreadError, match="not a handoff"):
            await handle_thread_accept_handoff(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="whoever",
                args={"handoff_id": nid},
                project_path=tmp_path,
            )


# ---- Phase 5 Task C: evaluator routing + identity guard -------------------


class TestPhase5EvaluatorRouting:
    """New-shape ``evaluator`` specs (doc 10) and the structural
    ``evaluator ≠ completing actor`` guard."""

    @pytest.mark.asyncio
    async def test_specific_human_accepts(self, tmp_path: Path) -> None:
        from jig.models import SpecificHumanEvaluator

        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(
                    name="implement",
                    role="dev",
                    evaluator=SpecificHumanEvaluator(
                        type="specific_human", user="alice"
                    ),
                ),
                PhaseConfig(name="review", role="reviewer"),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets, threads=threads, bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        # "reviewer" is the natural-sequence next role, but the
        # explicit spec names alice — reviewer must be refused.
        with pytest.raises(ThreadError, match="phase evaluator"):
            await handle_thread_accept_handoff(
                tickets=tickets, threads=threads, bus=bus,
                sender="reviewer",
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )
        await handle_thread_accept_handoff(
            tickets=tickets, threads=threads, bus=bus,
            sender="alice",
            args={"handoff_id": handoff["handoff_id"]},
            project_path=tmp_path,
        )
        h = await threads.get(handoff["handoff_id"])
        assert isinstance(h, Handoff)
        assert h.accepted_by == "alice"

    @pytest.mark.asyncio
    async def test_automated_only_rejects_manual(
        self, tmp_path: Path
    ) -> None:
        from jig.models import AutomatedOnlyEvaluator

        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(
                    name="run-tests",
                    role="ci",
                    evaluator=AutomatedOnlyEvaluator(
                        type="automated_only"
                    ),
                ),
                PhaseConfig(name="done", role="po"),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets, threads=threads, bus=bus,
            sender="ci",
            args={
                "ticket_id": ticket_id,
                "phase": "run-tests",
                "outputs": [],
            },
        )
        with pytest.raises(ThreadError, match="automated_only"):
            await handle_thread_accept_handoff(
                tickets=tickets, threads=threads, bus=bus,
                sender="po",
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_identity_guard_blocks_self_accept(
        self, tmp_path: Path
    ) -> None:
        """Even when ``specific_role`` names the completing phase's
        own role, the handoff author can't self-certify."""
        from jig.models import SpecificRoleEvaluator

        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(
                    name="implement",
                    role="dev",
                    evaluator=SpecificRoleEvaluator(
                        type="specific_role", role="dev"
                    ),
                ),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets, threads=threads, bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        # Sender matches the evaluator role AND the handoff author —
        # identity guard refuses.
        with pytest.raises(
            ThreadError, match="cannot be the completing actor"
        ):
            await handle_thread_accept_handoff(
                tickets=tickets, threads=threads, bus=bus,
                sender="dev",
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_multi_any_member_accepts(self, tmp_path: Path) -> None:
        """With a ``multi`` spec, any listed actor can accept (the
        shared guard matches sender against the merged actor list;
        enforcement of all-must-accept semantics lands in Task D)."""
        from jig.models import (
            MultiEvaluator,
            SpecificHumanEvaluator,
            SpecificRoleEvaluator,
        )

        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(
                    name="implement",
                    role="dev",
                    evaluator=MultiEvaluator(
                        type="multi",
                        evaluators=[
                            SpecificRoleEvaluator(
                                type="specific_role", role="reviewer"
                            ),
                            SpecificHumanEvaluator(
                                type="specific_human", user="alice"
                            ),
                        ],
                    ),
                ),
            ],
        )
        handoff = await handle_thread_handoff(
            tickets=tickets, threads=threads, bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        # A third-party sender is refused.
        with pytest.raises(ThreadError, match="phase evaluator"):
            await handle_thread_accept_handoff(
                tickets=tickets, threads=threads, bus=bus,
                sender="stranger",
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )
        # Either listed actor is accepted (Task D will tighten to
        # all-must-accept).
        await handle_thread_accept_handoff(
            tickets=tickets, threads=threads, bus=bus,
            sender="reviewer",
            args={"handoff_id": handoff["handoff_id"]},
            project_path=tmp_path,
        )
        h = await threads.get(handoff["handoff_id"])
        assert isinstance(h, Handoff)
        assert h.accepted_by == "reviewer"

    @pytest.mark.asyncio
    async def test_previous_phase_role_resolves(
        self, tmp_path: Path
    ) -> None:
        """``previous_phase_role`` resolves to the concrete actor
        who accepted the earlier phase's handoff."""
        from jig.models import PreviousPhaseRoleEvaluator

        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_workflow(
            tmp_path,
            phases=[
                PhaseConfig(name="spec", role="po"),
                PhaseConfig(
                    name="implement",
                    role="dev",
                    evaluator=PreviousPhaseRoleEvaluator(
                        type="previous_phase_role", role="po"
                    ),
                ),
            ],
        )
        # Seed an accepted prior-phase Handoff in the store so the
        # resolver has something to find.
        first = await handle_thread_handoff(
            tickets=tickets, threads=threads, bus=bus,
            sender="po",
            args={"ticket_id": ticket_id, "phase": "spec", "outputs": []},
        )
        # No explicit evaluator on "spec", so next phase's role (dev)
        # would normally accept — but that breaks the identity guard
        # when used later. Use a bespoke reviewer for this seed.
        await handle_thread_accept_handoff(
            tickets=tickets, threads=threads, bus=bus,
            sender="dev",  # natural-sequence next role
            args={"handoff_id": first["handoff_id"]},
            project_path=tmp_path,
        )
        # Now implement phase closes.
        handoff = await handle_thread_handoff(
            tickets=tickets, threads=threads, bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
        )
        # The previous po-phase handoff was accepted by "dev". So
        # previous_phase_role(po) resolves to "dev" — but "dev" is
        # also the implement-phase handoff author, so the identity
        # guard refuses. This is the intended safety: the structural
        # rule overrides a would-be self-certification even when
        # the spec resolves to the completing actor.
        with pytest.raises(
            ThreadError, match="cannot be the completing actor"
        ):
            await handle_thread_accept_handoff(
                tickets=tickets, threads=threads, bus=bus,
                sender="dev",
                args={"handoff_id": handoff["handoff_id"]},
                project_path=tmp_path,
            )


# ---- thread_waive_check (Phase 5 Task E) ----------------------------------


async def _seed_check_failure(
    threads: ThreadStore,
    *,
    ticket_id: str,
    check_name: str = "unit",
    severity: str = "required",
    verdict: str = "fail",
) -> str:
    """Post a ``check_failure`` SystemEvent and return its id.

    Mirrors the shape the gate (``jig.check_gate.evaluate_handoff_gate``)
    writes when a required check fails.
    """
    return await threads.post(
        SystemEvent(
            ticket_id=ticket_id,
            author="harness",
            event_type="check_failure",
            content=f"Required check {check_name!r} did not pass",
            check_name=check_name,
            check_severity=severity,  # type: ignore[arg-type]
            check_verdict=verdict,  # type: ignore[arg-type]
        )
    )


class TestThreadWaiveCheck:
    @pytest.mark.asyncio
    async def test_waive_by_check_failure_id(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)  # default authority = ["po", "sa", "user"]
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        result = await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            args={
                "check_failure_id": fid,
                "justification": "flaky on CI, tracked in follow-up",
            },
            project_path=tmp_path,
        )
        waiver = await threads.get(result["waiver_id"])
        assert isinstance(waiver, Waiver)
        assert waiver.check_failure_id == fid
        assert waiver.objection_id is None
        assert waiver.author == "po"
        assert (
            waiver.justification == "flaky on CI, tracked in follow-up"
        )
        ev = await threads.get(fid)
        assert isinstance(ev, SystemEvent)
        assert ev.waived is True
        assert result["check_name"] == "unit"
        assert result["waived_by"] == "po"
        assert result["check_failure_id"] == fid

    @pytest.mark.asyncio
    async def test_waive_by_ticket_and_check_name(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        old = await _seed_check_failure(
            threads, ticket_id=ticket_id, check_name="unit"
        )
        new = await _seed_check_failure(
            threads, ticket_id=ticket_id, check_name="unit"
        )
        assert old != new
        result = await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            args={
                "ticket_id": ticket_id,
                "check_name": "unit",
                "justification": "will fix next sprint",
            },
            project_path=tmp_path,
        )
        assert result["check_failure_id"] == new
        ev_new = await threads.get(new)
        ev_old = await threads.get(old)
        assert isinstance(ev_new, SystemEvent)
        assert isinstance(ev_old, SystemEvent)
        assert ev_new.waived is True
        assert ev_old.waived is False

    @pytest.mark.asyncio
    async def test_unauthorized_sender_refused(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path, waiver_authority=["po", "sa"])
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        with pytest.raises(ThreadError, match="not authorized"):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                args={
                    "check_failure_id": fid,
                    "justification": "I promise it's fine",
                },
                project_path=tmp_path,
            )
        ev = await threads.get(fid)
        assert isinstance(ev, SystemEvent)
        assert ev.waived is False

    @pytest.mark.asyncio
    async def test_empty_justification_rejected(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        with pytest.raises(ValueError, match="justification"):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                args={
                    "check_failure_id": fid,
                    "justification": "   ",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_already_waived_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            args={
                "check_failure_id": fid,
                "justification": "first waive",
            },
            project_path=tmp_path,
        )
        with pytest.raises(ThreadError, match="already waived"):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                args={
                    "check_failure_id": fid,
                    "justification": "second waive",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_non_check_failure_entry_rejected(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        note_id = await threads.post(
            Note(ticket_id=ticket_id, author="dev", text="hi")
        )
        with pytest.raises(ThreadError, match="not a check_failure"):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                args={
                    "check_failure_id": note_id,
                    "justification": "wrong target",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_name_lookup_with_no_match_raises(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        with pytest.raises(
            ThreadError, match="no unwaived check_failure"
        ):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                args={
                    "ticket_id": ticket_id,
                    "check_name": "unit",
                    "justification": "nothing to waive yet",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_missing_args_rejected(self, tmp_path: Path) -> None:
        tickets, threads, bus, _ = await _make_stores(tmp_path)
        _write_config(tmp_path)
        with pytest.raises(
            ValueError, match="check_failure_id or"
        ):
            await handle_thread_waive_check(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="po",
                args={"justification": "no target"},
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_publishes_to_bus(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)
        fid = await _seed_check_failure(threads, ticket_id=ticket_id)
        await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            args={
                "check_failure_id": fid,
                "justification": "known flake",
            },
            project_path=tmp_path,
        )
        msgs = await bus.get_history(f"tickets.{ticket_id}")
        match = [
            m for m in msgs
            if m.payload.get("kind") == "thread_check_failure_waived"
        ]
        assert len(match) == 1
        payload = match[0].payload
        assert payload["check_failure_id"] == fid
        assert payload["check_name"] == "unit"
        assert payload["waived_by"] == "po"
        assert payload["ticket_id"] == ticket_id

    @pytest.mark.asyncio
    async def test_gate_respects_waived_flag(self, tmp_path: Path) -> None:
        """Integration: once a check_failure is waived, the handoff
        gate sees it as non-blocking on the next run."""
        from datetime import datetime, timedelta, timezone

        from jig.check_gate import evaluate_handoff_gate
        from jig.check_results import CheckResult
        from jig.checks import CheckCatalog, CheckSeverity, ScriptedCheck
        from jig.store.check_results import CheckResultsStore

        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        _write_config(tmp_path)

        results = CheckResultsStore(tmp_path / "check_results.jsonl")
        await results.load()
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        sha = "deadbeef" * 5
        await results.post(
            CheckResult(
                ticket_id=ticket_id,
                phase="dev",
                check_name="unit",
                check_type="scripted",
                verdict="fail",
                severity=CheckSeverity.REQUIRED,
                started_at=base,
                finished_at=base + timedelta(seconds=1),
                output="",
                commit_sha=sha,
            )
        )
        catalog = CheckCatalog.model_validate(
            {
                "unit": ScriptedCheck(
                    type="scripted",
                    command="true",
                    severity=CheckSeverity.REQUIRED,
                ).model_dump()
            }
        )

        v1 = await evaluate_handoff_gate(
            catalog=catalog,
            results=results,
            threads=threads,
            ticket_id=ticket_id,
            phase="dev",
            required_check_names=["unit"],
        )
        assert v1.passing is False
        assert len(v1.posted_events) == 1
        fid = v1.posted_events[0]

        await handle_thread_waive_check(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="po",
            args={
                "check_failure_id": fid,
                "justification": "will fix in follow-up",
            },
            project_path=tmp_path,
        )

        v2 = await evaluate_handoff_gate(
            catalog=catalog,
            results=results,
            threads=threads,
            ticket_id=ticket_id,
            phase="dev",
            required_check_names=["unit"],
        )
        assert v2.passing is True
        assert v2.posted_events == []
