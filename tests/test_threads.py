"""Tests for ThreadStore (Phase 4 Task B).

Covers three orthogonal concerns:

* **New-shape post/read roundtrip** — ThreadStore writes typed
  ThreadEntry dicts; reading them back yields the same typed model.
* **Legacy Comment migration on read** — records written in the Phase 3
  Comment shape (including the 3E proposal envelope and the
  ``commit``/``phase_run``/``status_change`` system primitives) surface
  as the new typed entries after migration.
* **Gating helpers** — ``has_unresolved_blocking`` and ``find_by_kind``
  drive the dispatch layer (Task H); they need to reflect the resolved
  semantics for each entry type.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jig.store.collection import Collection
from jig.store.threads import ThreadStore
from jig.thread import (
    Handoff,
    Note,
    Objection,
    Proposal,
    Question,
    SystemEvent,
)


# ---- helpers --------------------------------------------------------------


async def _raw_insert(path: Path, raw: dict) -> str:
    """Insert a legacy-shape dict directly, bypassing ThreadStore.

    The migration path only triggers on read, so we need a way to plant
    records that pre-date the Phase 4 schema. Using the same index
    fields as ThreadStore so lookups go through the same code path.
    """
    col = Collection(path, index_fields=["ticket_id", "kind", "author"])
    await col.load()
    return await col.insert(raw)


# ---- new-shape roundtrip --------------------------------------------------


class TestPostAndRead:
    @pytest.mark.asyncio
    async def test_post_note_roundtrip(self, tmp_path: Path) -> None:
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        note = Note(ticket_id="T1", author="alice", text="hello")
        eid = await store.post(note)
        assert eid == note.id
        entries = await store.for_ticket("T1")
        assert len(entries) == 1
        assert isinstance(entries[0], Note)
        assert entries[0].text == "hello"
        assert entries[0].id == note.id

    @pytest.mark.asyncio
    async def test_post_various_kinds(self, tmp_path: Path) -> None:
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        await store.post(Note(ticket_id="T1", author="a", text="n"))
        await store.post(
            Question(
                ticket_id="T1",
                author="a",
                target="b",
                question="?",
                blocking=True,
            )
        )
        await store.post(
            Objection(
                ticket_id="T1",
                author="r",
                target_artifact="f.py",
                text="nope",
            )
        )
        await store.post(
            Handoff(
                ticket_id="T1",
                author="a",
                phase="implement",
                outputs=["f.py"],
                summary="done",
            )
        )
        entries = await store.for_ticket("T1")
        kinds = sorted(e.kind for e in entries)
        assert kinds == ["handoff", "note", "objection", "question"]

    @pytest.mark.asyncio
    async def test_for_ticket_is_chronological(self, tmp_path: Path) -> None:
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        t0 = datetime.now(timezone.utc)
        await store.post(
            Note(
                ticket_id="T1",
                author="a",
                text="second",
                created_at=t0 + timedelta(seconds=1),
            )
        )
        await store.post(
            Note(ticket_id="T1", author="a", text="first", created_at=t0)
        )
        entries = await store.for_ticket("T1")
        assert [e.text for e in entries] == ["first", "second"]  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_get_by_id(self, tmp_path: Path) -> None:
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        n = Note(ticket_id="T1", author="a", text="x")
        eid = await store.post(n)
        loaded = await store.get(eid)
        assert isinstance(loaded, Note)
        assert loaded.id == eid

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        assert await store.get("missing") is None


# ---- update ---------------------------------------------------------------


class TestUpdate:
    @pytest.mark.asyncio
    async def test_close_question_via_update(self, tmp_path: Path) -> None:
        """``thread_close_question`` flips ``resolved_by`` — Task D will
        call ``update``. The store itself doesn't care about semantics;
        it just patches."""
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        q = Question(
            ticket_id="T1",
            author="asker",
            target="bob",
            question="?",
            blocking=True,
        )
        eid = await store.post(q)
        # Blocking before resolution.
        blocking = await store.has_unresolved_blocking("T1")
        assert len(blocking) == 1

        ok = await store.update(eid, {"resolved_by": "asker"})
        assert ok is True

        blocking = await store.has_unresolved_blocking("T1")
        assert blocking == []

    @pytest.mark.asyncio
    async def test_update_missing_entry_returns_false(
        self, tmp_path: Path
    ) -> None:
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        assert await store.update("missing", {"resolved_by": "x"}) is False


# ---- legacy migration -----------------------------------------------------


class TestLegacyMigration:
    @pytest.mark.asyncio
    async def test_legacy_commit_becomes_system_event(
        self, tmp_path: Path
    ) -> None:
        """Phase 3 wrote ``Comment(kind="commit", commit_sha=...)``; the
        Phase 4 store surfaces it as SystemEvent(event_type="commit")."""
        path = tmp_path / "comments.jsonl"
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "harness",
                "content": "commit abc",
                "kind": "commit",
                "commit_sha": "abc123",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store = ThreadStore(path)
        await store.load()
        entries = await store.for_ticket("T1")
        assert len(entries) == 1
        ev = entries[0]
        assert isinstance(ev, SystemEvent)
        assert ev.event_type == "commit"
        assert ev.commit_sha == "abc123"
        assert ev.is_resolved()
        assert not ev.is_blocking()

    @pytest.mark.asyncio
    async def test_legacy_phase_run_becomes_system_event(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "comments.jsonl"
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "harness",
                "content": "phase done",
                "kind": "phase_run",
                "phase_result": "success",
                "phase_branch": "implement",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store = ThreadStore(path)
        await store.load()
        [ev] = await store.for_ticket("T1")
        assert isinstance(ev, SystemEvent)
        assert ev.event_type == "phase_run"
        assert ev.phase_result == "success"
        assert ev.phase_branch == "implement"

    @pytest.mark.asyncio
    async def test_legacy_status_change_becomes_system_event(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "comments.jsonl"
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "harness",
                "content": "open -> in_progress",
                "kind": "status_change",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store = ThreadStore(path)
        await store.load()
        [ev] = await store.for_ticket("T1")
        assert isinstance(ev, SystemEvent)
        assert ev.event_type == "status_change"
        assert ev.content == "open -> in_progress"

    @pytest.mark.asyncio
    async def test_legacy_comment_becomes_note(self, tmp_path: Path) -> None:
        path = tmp_path / "comments.jsonl"
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "alice",
                "content": "hello",
                "kind": "comment",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store = ThreadStore(path)
        await store.load()
        [n] = await store.for_ticket("T1")
        assert isinstance(n, Note)
        assert n.text == "hello"

    @pytest.mark.asyncio
    async def test_legacy_question_shape_migrates(
        self, tmp_path: Path
    ) -> None:
        """Old question comments had ``content`` but no typed
        ``question`` field; the migration pulls ``content`` across."""
        path = tmp_path / "comments.jsonl"
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "alice",
                "content": "why?",
                "kind": "question",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store = ThreadStore(path)
        await store.load()
        [q] = await store.for_ticket("T1")
        assert isinstance(q, Question)
        assert q.question == "why?"
        assert q.target == "any_human"  # default

    @pytest.mark.asyncio
    async def test_legacy_answer_shape_migrates(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "comments.jsonl"
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "bob",
                "content": "because",
                "kind": "answer",
                "question_id": "q-1",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store = ThreadStore(path)
        await store.load()
        [a] = await store.for_ticket("T1")
        assert a.kind == "answer"
        assert a.text == "because"  # type: ignore[attr-defined]
        assert a.question_id == "q-1"  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_legacy_decision_shape_migrates(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "comments.jsonl"
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "alice",
                "content": "use X",
                "kind": "decision",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store = ThreadStore(path)
        await store.load()
        [d] = await store.for_ticket("T1")
        assert d.kind == "decision"
        assert d.decision == "use X"  # type: ignore[attr-defined]
        assert d.rationale == ""  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_legacy_proposal_envelope_migrates(
        self, tmp_path: Path
    ) -> None:
        """Phase 3E stashed proposal fields on the Comment envelope
        with ``proposal_*`` prefixes. Task B renames them."""
        path = tmp_path / "comments.jsonl"
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "alice",
                "content": "change summary",
                "kind": "proposal",
                "proposal_target": "ticket://spec.summary",
                "proposal_section": "overview",
                "proposal_change": "new prose",
                "proposal_state": "accepted",
                "proposal_parent_id": None,
                "proposal_owners": ["po"],
                "proposal_spec_version": 2,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        store = ThreadStore(path)
        await store.load()
        [p] = await store.for_ticket("T1")
        assert isinstance(p, Proposal)
        assert p.target == "ticket://spec.summary"
        assert p.section == "overview"
        assert p.change == "new prose"
        assert p.state == "accepted"
        assert p.owners == ["po"]
        assert p.spec_version == 2
        assert p.rationale == "change summary"  # Comment.content carried.
        assert p.is_resolved()

    @pytest.mark.asyncio
    async def test_new_shape_passes_through(self, tmp_path: Path) -> None:
        """Migration is idempotent — a record already in the new shape
        must round-trip unchanged."""
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        n = Note(ticket_id="T1", author="a", text="new shape")
        await store.post(n)
        # Reading back exercises the migration path.
        [back] = await store.for_ticket("T1")
        assert isinstance(back, Note)
        assert back.id == n.id
        assert back.text == "new shape"


# ---- gating helpers -------------------------------------------------------


class TestGatingHelpers:
    @pytest.mark.asyncio
    async def test_has_unresolved_blocking_collects_blockers(
        self, tmp_path: Path
    ) -> None:
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        # Non-blocking: note + resolved question + accepted handoff +
        # non-blocking question.
        await store.post(Note(ticket_id="T1", author="a", text="x"))
        await store.post(
            Question(
                ticket_id="T1",
                author="a",
                target="b",
                question="?",
                blocking=True,
                resolved_by="a",
            )
        )
        await store.post(
            Handoff(
                ticket_id="T1",
                author="a",
                phase="implement",
                summary="done",
                acceptance_state="accepted",
                accepted_by="e",
            )
        )
        await store.post(
            Question(
                ticket_id="T1",
                author="a",
                target="b",
                question="np",
                blocking=False,
            )
        )
        # Blocking: open objection + pending handoff + open blocking q.
        await store.post(
            Objection(
                ticket_id="T1",
                author="r",
                target_artifact="f",
                text="nope",
            )
        )
        await store.post(
            Handoff(
                ticket_id="T1",
                author="a",
                phase="review",
                summary="please",
            )
        )
        await store.post(
            Question(
                ticket_id="T1",
                author="a",
                target="b",
                question="blocker?",
                blocking=True,
            )
        )

        blockers = await store.has_unresolved_blocking("T1")
        assert len(blockers) == 3
        kinds = sorted(e.kind for e in blockers)
        assert kinds == ["handoff", "objection", "question"]

    @pytest.mark.asyncio
    async def test_has_unresolved_blocking_scoped_per_ticket(
        self, tmp_path: Path
    ) -> None:
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        await store.post(
            Objection(
                ticket_id="T1",
                author="r",
                target_artifact="f",
                text="nope",
            )
        )
        await store.post(
            Objection(
                ticket_id="T2",
                author="r",
                target_artifact="f",
                text="nope2",
            )
        )
        assert len(await store.has_unresolved_blocking("T1")) == 1
        assert len(await store.has_unresolved_blocking("T2")) == 1

    @pytest.mark.asyncio
    async def test_find_by_kind_matches_migrated_system_event(
        self, tmp_path: Path
    ) -> None:
        """Legacy commit/phase_run records surface as system_event after
        migration — find_by_kind operates on the new kinds."""
        path = tmp_path / "comments.jsonl"
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "harness",
                "content": "c",
                "kind": "commit",
                "commit_sha": "abc",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )
        await _raw_insert(
            path,
            {
                "ticket_id": "T1",
                "author": "harness",
                "content": "p",
                "kind": "phase_run",
                "phase_result": "success",
                "created_at": "2026-01-01T00:00:01+00:00",
            },
        )
        store = ThreadStore(path)
        await store.load()
        events = await store.find_by_kind("T1", "system_event")
        assert len(events) == 2
        assert all(isinstance(e, SystemEvent) for e in events)
        # The legacy "commit" kind query against find_by_kind should
        # come back empty — the migration renamed it.
        assert await store.find_by_kind("T1", "commit") == []

    @pytest.mark.asyncio
    async def test_find_by_kind_filters_new_shape(
        self, tmp_path: Path
    ) -> None:
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        await store.post(Note(ticket_id="T1", author="a", text="n"))
        await store.post(
            Question(
                ticket_id="T1",
                author="a",
                target="b",
                question="?",
            )
        )
        notes = await store.find_by_kind("T1", "note")
        assert len(notes) == 1
        assert isinstance(notes[0], Note)

    async def test_all_by_kind_crosses_tickets(
        self, tmp_path: Path
    ) -> None:
        """`all_by_kind` returns matching entries across every ticket."""
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        await store.post(Note(ticket_id="T1", author="a", text="n1"))
        await store.post(Note(ticket_id="T2", author="a", text="n2"))
        await store.post(
            Question(
                ticket_id="T1",
                author="a",
                target="b",
                question="?",
            )
        )

        notes = await store.all_by_kind("note")
        assert {n.ticket_id for n in notes} == {"T1", "T2"}
        assert all(isinstance(n, Note) for n in notes)

        questions = await store.all_by_kind("question")
        assert len(questions) == 1
        assert questions[0].ticket_id == "T1"

    async def test_all_by_kind_empty(self, tmp_path: Path) -> None:
        """No matching entries yields an empty list, not an error."""
        store = ThreadStore(tmp_path / "comments.jsonl")
        await store.load()
        assert await store.all_by_kind("proposal") == []
