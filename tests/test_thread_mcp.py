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
from jig.project import Project
from jig.store import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Answer, Objection, Question, Resolution, Waiver
from jig.thread_mcp import (
    ThreadError,
    handle_thread_accept_resolution,
    handle_thread_answer,
    handle_thread_ask,
    handle_thread_object,
    handle_thread_resolve_objection,
    handle_thread_resolve_question,
    handle_thread_waive,
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
