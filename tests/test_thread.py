"""Tests for the typed thread-entry model (Phase 4 Task A).

Covers:

* Every type constructs with sensible defaults.
* ``is_blocking`` / ``is_resolved`` match doc 08's gating table.
* The discriminated union routes raw dicts to the right subtype.
* Roundtrip through ``model_dump(mode="json")`` + ``parse_thread_entry``
  preserves payload.
* Proposal carries the Phase 3E fields forward.
* Unknown ``kind`` fails loud rather than silently becoming a Note.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jig.thread import (
    Answer,
    Decision,
    DeferredItem,
    Escalation,
    Handoff,
    Note,
    Objection,
    Proposal,
    Question,
    Resolution,
    SystemEvent,
    Uncertain,
    Waiver,
    parse_thread_entry,
)


# ---- construction ---------------------------------------------------------


class TestConstruction:
    def test_note_defaults(self) -> None:
        n = Note(ticket_id="t-1", author="alice", text="noted")
        assert n.kind == "note"
        assert n.is_resolved()
        assert not n.is_blocking()

    def test_question_defaults_non_blocking(self) -> None:
        q = Question(
            ticket_id="t-1",
            author="alice",
            target="bob",
            question="why?",
        )
        assert q.kind == "question"
        assert q.blocking is False
        assert not q.is_resolved()
        assert not q.is_blocking()  # non-blocking questions don't gate

    def test_blocking_question_gates(self) -> None:
        q = Question(
            ticket_id="t-1",
            author="alice",
            target="bob",
            question="why?",
            blocking=True,
        )
        assert q.is_blocking()
        q.resolved_by = "alice"
        assert q.is_resolved()
        assert not q.is_blocking()

    def test_objection_always_blocking_until_closed(self) -> None:
        o = Objection(
            ticket_id="t-1",
            author="alice",
            target_artifact="file.py",
            text="nope",
        )
        assert o.is_blocking()
        assert not o.is_resolved()

    def test_objection_resolves_via_resolver(self) -> None:
        o = Objection(
            ticket_id="t-1",
            author="alice",
            target_artifact="f",
            text="x",
            resolved_by="alice",
        )
        assert o.is_resolved()
        assert not o.is_blocking()

    def test_objection_resolves_via_waiver(self) -> None:
        o = Objection(
            ticket_id="t-1",
            author="alice",
            target_artifact="f",
            text="x",
            waived_by="sam",
        )
        assert o.is_resolved()
        assert not o.is_blocking()

    def test_escalation_blocks_until_resolved(self) -> None:
        e = Escalation(
            ticket_id="t-1",
            author="alice",
            reason="scope",
            details="architecture change needed",
        )
        assert e.is_blocking()
        e.resolved_by = "pam"
        assert not e.is_blocking()

    def test_handoff_blocks_until_accepted(self) -> None:
        h = Handoff(
            ticket_id="t-1",
            author="alice",
            phase="implement",
            summary="done",
            outputs=["main.py"],
        )
        assert h.is_blocking()
        assert h.acceptance_state == "pending"
        h.acceptance_state = "accepted"
        h.accepted_by = "pam"
        assert h.is_resolved()
        assert not h.is_blocking()

    def test_handoff_rejected_is_resolved(self) -> None:
        """A rejected handoff stops blocking advancement; the phase
        loops back per the workflow's on-failure edge (Task F)."""
        h = Handoff(
            ticket_id="t-1",
            author="alice",
            phase="implement",
            acceptance_state="rejected",
            rejection_reason="tests fail",
        )
        assert h.is_resolved()
        assert not h.is_blocking()

    def test_decision_auto_resolved(self) -> None:
        d = Decision(
            ticket_id="t-1",
            author="alice",
            decision="use X",
            rationale="cheaper",
        )
        assert d.is_resolved()
        assert not d.is_blocking()

    def test_answer_auto_resolved(self) -> None:
        a = Answer(
            ticket_id="t-1",
            author="bob",
            question_id="q-1",
            text="because",
        )
        assert a.is_resolved()
        assert not a.is_blocking()

    def test_waiver_auto_resolved(self) -> None:
        w = Waiver(
            ticket_id="t-1",
            author="sam",
            objection_id="o-1",
            justification="shipping pressure",
        )
        assert w.is_resolved()

    def test_resolution_auto_resolved_but_objection_not(self) -> None:
        """Resolution is inert until the objector accepts it.

        The typed model doesn't cross-reference the Objection; the
        MCP layer (Task D) handles the two-step close.
        """
        r = Resolution(
            ticket_id="t-1",
            author="alice",
            objection_id="o-1",
            text="fixed",
        )
        assert r.is_resolved()

    def test_uncertain_not_blocking(self) -> None:
        u = Uncertain(
            ticket_id="t-1",
            author="alice",
            details="not sure who to ask",
        )
        assert u.is_resolved()
        assert not u.is_blocking()


# ---- Proposal migration from Phase 3E -------------------------------------


class TestProposal:
    def test_pending_not_resolved(self) -> None:
        p = Proposal(
            ticket_id="t-1",
            author="alice",
            target="ticket://spec.behaviors",
            change="new B1",
        )
        assert p.kind == "proposal"
        assert p.state == "pending"
        assert not p.is_resolved()

    def test_refining_still_open(self) -> None:
        p = Proposal(
            ticket_id="t-1",
            author="alice",
            target="ticket://spec",
            state="refining",
        )
        assert not p.is_resolved()

    def test_accepted_or_rejected_resolved(self) -> None:
        p = Proposal(
            ticket_id="t-1",
            author="alice",
            target="ticket://spec",
            state="accepted",
        )
        assert p.is_resolved()
        p2 = Proposal(
            ticket_id="t-1",
            author="alice",
            target="ticket://spec",
            state="rejected",
        )
        assert p2.is_resolved()

    def test_proposal_does_not_block_phase(self) -> None:
        """Proposals route to owners; gating a phase on a proposal
        is an Escalation or a blocking Question's job."""
        p = Proposal(
            ticket_id="t-1",
            author="alice",
            target="ticket://spec",
        )
        assert not p.is_blocking()

    def test_spec_version_carried(self) -> None:
        p = Proposal(
            ticket_id="t-1",
            author="alice",
            target="ticket://spec.summary",
            state="accepted",
            spec_version=3,
            owners=["po"],
        )
        assert p.spec_version == 3
        assert p.owners == ["po"]


# ---- SystemEvent ----------------------------------------------------------


class TestSystemEvent:
    def test_commit_event(self) -> None:
        ev = SystemEvent(
            ticket_id="t-1",
            author="harness",
            event_type="commit",
            commit_sha="abc123",
        )
        assert ev.kind == "system_event"
        assert ev.is_resolved()
        assert not ev.is_blocking()

    def test_phase_run_event(self) -> None:
        ev = SystemEvent(
            ticket_id="t-1",
            author="harness",
            event_type="phase_run",
            phase_result="success",
            phase_branch="implement",
        )
        assert ev.phase_result == "success"

    def test_invalid_event_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SystemEvent(
                ticket_id="t-1",
                author="harness",
                event_type="random",  # type: ignore[arg-type]
            )


# ---- discriminated union --------------------------------------------------


class TestDiscriminatedUnion:
    def test_parse_dispatches_by_kind(self) -> None:
        raw = {
            "ticket_id": "t-1",
            "author": "alice",
            "kind": "question",
            "target": "bob",
            "question": "why?",
        }
        entry = parse_thread_entry(raw)
        assert isinstance(entry, Question)
        assert entry.target == "bob"

    def test_parse_objection(self) -> None:
        raw = {
            "ticket_id": "t-1",
            "author": "alice",
            "kind": "objection",
            "target_artifact": "main.py",
            "text": "wrong",
        }
        entry = parse_thread_entry(raw)
        assert isinstance(entry, Objection)

    def test_parse_handoff_with_deferred_items(self) -> None:
        raw = {
            "ticket_id": "t-1",
            "author": "alice",
            "kind": "handoff",
            "phase": "implement",
            "outputs": ["main.py"],
            "summary": "done",
            "deferred_items": [
                {"item": "extract helper", "reason": "scope creep"},
            ],
        }
        entry = parse_thread_entry(raw)
        assert isinstance(entry, Handoff)
        assert len(entry.deferred_items) == 1
        assert entry.deferred_items[0].status == "open"

    def test_unknown_kind_fails_loud(self) -> None:
        with pytest.raises(ValidationError):
            parse_thread_entry(
                {
                    "ticket_id": "t-1",
                    "author": "alice",
                    "kind": "mumble",
                    "content": "x",
                }
            )

    def test_missing_kind_fails(self) -> None:
        with pytest.raises(ValidationError):
            parse_thread_entry(
                {"ticket_id": "t-1", "author": "alice", "text": "hi"}
            )


# ---- roundtrip ------------------------------------------------------------


class TestRoundtrip:
    def test_question_roundtrip(self) -> None:
        q = Question(
            ticket_id="t-1",
            author="alice",
            target="bob",
            question="why?",
            blocking=True,
        )
        raw = q.model_dump(mode="json", by_alias=True)
        back = parse_thread_entry(raw)
        assert isinstance(back, Question)
        assert back.question == "why?"
        assert back.blocking is True
        assert back.id == q.id

    def test_handoff_roundtrip_with_nested_items(self) -> None:
        h = Handoff(
            ticket_id="t-1",
            author="alice",
            phase="implement",
            summary="done",
            outputs=["a.py", "b.py"],
            deferred_items=[
                DeferredItem(
                    item="refactor helper", reason="scope", status="open"
                ),
                DeferredItem(
                    item="better error msg",
                    reason="nice-to-have",
                    status="promoted",
                    promoted_ticket_id="t-99",
                ),
            ],
        )
        raw = h.model_dump(mode="json", by_alias=True)
        back = parse_thread_entry(raw)
        assert isinstance(back, Handoff)
        assert len(back.deferred_items) == 2
        assert back.deferred_items[1].promoted_ticket_id == "t-99"

    def test_proposal_roundtrip(self) -> None:
        p = Proposal(
            ticket_id="t-1",
            author="alice",
            target="ticket://spec.summary",
            change="new summary",
            owners=["po"],
            state="accepted",
            spec_version=2,
        )
        raw = p.model_dump(mode="json", by_alias=True)
        back = parse_thread_entry(raw)
        assert isinstance(back, Proposal)
        assert back.state == "accepted"
        assert back.spec_version == 2

    def test_system_event_roundtrip(self) -> None:
        ev = SystemEvent(
            ticket_id="t-1",
            author="harness",
            event_type="phase_run",
            phase_result="failed",
            phase_branch="implement",
        )
        raw = ev.model_dump(mode="json", by_alias=True)
        back = parse_thread_entry(raw)
        assert isinstance(back, SystemEvent)
        assert back.phase_result == "failed"
