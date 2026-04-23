"""Tests for Phase 4 Task G — checkpoint channel.

Covers:

* Checkpoint model roundtrip (pydantic validation, DeferredItem /
  RuledOut coercion).
* CheckpointStore queries: for_ticket, for_phase, latest,
  deferred_items_open.
* ``mark_phase_historical`` flips records and excludes from default
  queries while keeping include_historical=True readable.
* Agent MCP handlers (milestone / decision / deferred) write typed
  records and honor validation (unknown ticket, wrong entry type).
* Harness hooks (auto_commit / auto_test / auto_pre_handoff) land
  with the expected trigger + payload.
* handle_commit_progress records auto_test + auto_commit on success
  and auto_test on LintError.
* handle_thread_handoff auto-pulls open deferred items from
  checkpoints and records an auto_pre_handoff snapshot.
* Handoff accept marks phase checkpoints historical; reject leaves
  them live.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from jig.checkpoint_mcp import (
    CheckpointError,
    handle_checkpoint_decision,
    handle_checkpoint_deferred,
    handle_checkpoint_milestone,
    handle_checkpoint_promote_deferred,
    record_auto_commit_checkpoint,
    record_auto_pre_handoff_checkpoint,
    record_auto_test_checkpoint,
)
from jig.checkpoints import Checkpoint, RuledOut
from jig.models import PhaseConfig, WorkflowConfig
from jig.persistence import save_workflow
from jig.store.bus import MessageBus
from jig.store.checkpoints import CheckpointStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import DeferredItem, Decision, Note
from jig.thread_mcp import (
    handle_thread_accept_handoff,
    handle_thread_handoff,
    handle_thread_reject_handoff,
)
from jig.ticket import Ticket, WorkType


# ---- fixtures -------------------------------------------------------------


async def _make_stores(
    tmp_path: Path,
) -> tuple[TicketStore, ThreadStore, CheckpointStore, MessageBus, str]:
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    checkpoints = CheckpointStore(tmp_path / "checkpoints.jsonl")
    await checkpoints.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    ticket_id = await tickets.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="orchestrator",
        )
    )
    return tickets, threads, checkpoints, bus, ticket_id


# ---- model + store --------------------------------------------------------


class TestCheckpointModel:
    def test_defaults_and_required(self) -> None:
        cp = Checkpoint(
            ticket_id="t1",
            phase="implement",
            author="dev",
            trigger="agent_milestone",
        )
        assert cp.description == ""
        assert cp.completed == []
        assert cp.deferred == []
        assert cp.historical is False

    def test_ruled_out_and_deferred_roundtrip(self) -> None:
        cp = Checkpoint(
            ticket_id="t1",
            phase="implement",
            author="dev",
            trigger="agent_milestone",
            ruled_out=[RuledOut(approach="cache in-memory", reason="too big")],
            deferred=[DeferredItem(item="rename columns", reason="later")],
        )
        data = cp.model_dump(mode="json")
        reborn = Checkpoint.model_validate(data)
        assert reborn.ruled_out[0].approach == "cache in-memory"
        assert reborn.deferred[0].item == "rename columns"


class TestCheckpointStore:
    @pytest.mark.asyncio
    async def test_post_and_for_ticket(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        cp = Checkpoint(
            ticket_id=ticket_id,
            phase="implement",
            author="dev",
            trigger="agent_milestone",
            description="first",
        )
        cid = await checkpoints.post(cp)
        assert cid
        got = await checkpoints.for_ticket(ticket_id)
        assert len(got) == 1
        assert got[0].description == "first"

    @pytest.mark.asyncio
    async def test_for_phase_scopes(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
                description="a",
            )
        )
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="review",
                author="rev",
                trigger="agent_milestone",
                description="b",
            )
        )
        impl = await checkpoints.for_phase(ticket_id, "implement")
        review = await checkpoints.for_phase(ticket_id, "review")
        assert [c.description for c in impl] == ["a"]
        assert [c.description for c in review] == ["b"]

    @pytest.mark.asyncio
    async def test_latest_returns_most_recent(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        first = await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
                description="first",
            )
        )
        second = await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
                description="second",
            )
        )
        assert first != second
        latest = await checkpoints.latest(ticket_id)
        assert latest is not None
        assert latest.description == "second"

    @pytest.mark.asyncio
    async def test_latest_phase_scoped(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
                description="impl-a",
            )
        )
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="review",
                author="rev",
                trigger="agent_milestone",
                description="rev-a",
            )
        )
        latest_impl = await checkpoints.latest(ticket_id, phase="implement")
        assert latest_impl is not None
        assert latest_impl.description == "impl-a"

    @pytest.mark.asyncio
    async def test_deferred_items_open_collects_from_phase(
        self, tmp_path: Path
    ) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_deferred",
                deferred=[DeferredItem(item="tidy config")],
            )
        )
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_deferred",
                deferred=[
                    DeferredItem(item="drop column", status="done"),
                    DeferredItem(item="rename bar"),
                ],
            )
        )
        items = await checkpoints.deferred_items_open(ticket_id, "implement")
        names = [i.item for i in items]
        assert names == ["tidy config", "rename bar"]

    @pytest.mark.asyncio
    async def test_mark_phase_historical_excludes_default_queries(
        self, tmp_path: Path
    ) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
                description="old",
            )
        )
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="review",
                author="rev",
                trigger="agent_milestone",
                description="current",
            )
        )

        flipped = await checkpoints.mark_phase_historical(ticket_id, "implement")
        assert flipped == 1

        # Default queries skip historical
        current_view = await checkpoints.for_ticket(ticket_id)
        assert [c.description for c in current_view] == ["current"]

        # Opt-in shows both
        full = await checkpoints.for_ticket(ticket_id, include_historical=True)
        assert {c.description for c in full} == {"old", "current"}

    @pytest.mark.asyncio
    async def test_mark_phase_historical_idempotent(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
            )
        )
        first = await checkpoints.mark_phase_historical(ticket_id, "implement")
        second = await checkpoints.mark_phase_historical(ticket_id, "implement")
        assert first == 1
        assert second == 0  # Already historical


class TestLegacyDeferredItemBackfill:
    """Legacy records written before ``DeferredItem.id`` was introduced
    don't carry an ``id`` field on their deferred items. Pydantic's
    ``default_factory`` would mint a fresh uuid4 on every load, which
    breaks ``checkpoint_promote_deferred`` across restarts.

    The backfill derives ``{checkpoint_id}:deferred:{index}`` at read
    time (and again on write via ``update_deferred_item``), healing
    the record the first time it's touched.
    """

    @pytest.mark.asyncio
    async def test_backfilled_id_is_stable_across_loads(
        self, tmp_path: Path
    ) -> None:
        """Two reads of the same legacy record yield the same id."""
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        # Raw insert bypassing the pydantic model — simulates a JSONL
        # record written before DeferredItem carried an id.
        legacy_raw = {
            "ticket_id": ticket_id,
            "phase": "implement",
            "author": "dev",
            "trigger": "agent_deferred",
            "description": "",
            "completed": [],
            "ruled_out": [],
            "deferred": [
                {"item": "no id on disk", "reason": "legacy", "status": "open"},
            ],
            "open_questions": [],
            "created_at": "2026-04-20T00:00:00+00:00",
            "historical": False,
            "plan": "",
            "position": "",
        }
        checkpoint_id = await checkpoints._collection.insert(legacy_raw)

        first_read = await checkpoints.for_ticket(ticket_id)
        second_read = await checkpoints.for_ticket(ticket_id)
        assert len(first_read) == 1
        assert len(second_read) == 1

        first_id = first_read[0].deferred[0].id
        second_id = second_read[0].deferred[0].id
        assert first_id == second_id
        # And it's the deterministic derivation, not a uuid4.
        assert first_id == f"{checkpoint_id}:deferred:0"

    @pytest.mark.asyncio
    async def test_update_deferred_item_works_with_derived_id(
        self, tmp_path: Path
    ) -> None:
        """Promotion (update_deferred_item) resolves the derived id and
        rewrites the record — the next read sees the backfilled id
        persisted, so subsequent updates don't rely on re-derivation."""
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        legacy_raw = {
            "ticket_id": ticket_id,
            "phase": "implement",
            "author": "dev",
            "trigger": "agent_deferred",
            "description": "",
            "completed": [],
            "ruled_out": [],
            "deferred": [
                {"item": "promote me", "reason": "", "status": "open"},
            ],
            "open_questions": [],
            "created_at": "2026-04-20T00:00:00+00:00",
            "historical": False,
            "plan": "",
            "position": "",
        }
        checkpoint_id = await checkpoints._collection.insert(legacy_raw)
        derived_id = f"{checkpoint_id}:deferred:0"

        ok = await checkpoints.update_deferred_item(
            checkpoint_id,
            derived_id,
            status="promoted",
            promoted_ticket_id="child-1",
        )
        assert ok is True

        # On-disk record now has the id baked in — the lazy migration
        # healed the record.
        raw = await checkpoints._collection.get(checkpoint_id)
        assert raw is not None
        assert raw["deferred"][0]["id"] == derived_id
        assert raw["deferred"][0]["status"] == "promoted"
        assert raw["deferred"][0]["promoted_ticket_id"] == "child-1"


# ---- agent MCP handlers ---------------------------------------------------


class TestCheckpointMilestone:
    @pytest.mark.asyncio
    async def test_writes_and_returns_id(self, tmp_path: Path) -> None:
        tickets, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        result = await handle_checkpoint_milestone(
            tickets=tickets,
            checkpoints=checkpoints,
            sender="dev",
            phase_name="implement",
            args={
                "ticket_id": ticket_id,
                "description": "finished step 1",
                "position": "mid-implementation",
                "plan": "next: step 2",
                "completed": ["schema", "migration"],
                "ruled_out": [
                    {"approach": "inline cache", "reason": "too heavy"},
                    "raw-string approach",
                ],
            },
        )
        cid = result["checkpoint_id"]
        cp = await checkpoints.get(cid)
        assert cp is not None
        assert cp.trigger == "agent_milestone"
        assert cp.author == "dev"
        assert cp.phase == "implement"
        assert cp.completed == ["schema", "migration"]
        assert len(cp.ruled_out) == 2
        assert cp.ruled_out[1].approach == "raw-string approach"
        assert cp.ruled_out[1].reason == ""

    @pytest.mark.asyncio
    async def test_unknown_ticket_raises(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, _ = await _make_stores(tmp_path)
        tickets = TicketStore(tmp_path / "other_tickets.jsonl")
        await tickets.load()
        with pytest.raises(CheckpointError, match="ticket .* not found"):
            await handle_checkpoint_milestone(
                tickets=tickets,
                checkpoints=checkpoints,
                sender="dev",
                phase_name="implement",
                args={"ticket_id": "nope"},
            )

    @pytest.mark.asyncio
    async def test_empty_phase_name_falls_back(self, tmp_path: Path) -> None:
        tickets, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        result = await handle_checkpoint_milestone(
            tickets=tickets,
            checkpoints=checkpoints,
            sender="dev",
            phase_name="",
            args={"ticket_id": ticket_id, "description": "x"},
        )
        cp = await checkpoints.get(result["checkpoint_id"])
        assert cp.phase == "<unknown>"


class TestCheckpointDecision:
    @pytest.mark.asyncio
    async def test_mirrors_thread_decision(self, tmp_path: Path) -> None:
        tickets, threads, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        dec = Decision(
            ticket_id=ticket_id,
            author="dev",
            decision="use pg over sqlite",
            rationale="concurrent writes matter",
        )
        did = await threads.post(dec)

        result = await handle_checkpoint_decision(
            tickets=tickets,
            threads=threads,
            checkpoints=checkpoints,
            sender="dev",
            phase_name="implement",
            args={"decision_id": did},
        )
        cp = await checkpoints.get(result["checkpoint_id"])
        assert cp.trigger == "agent_decision"
        assert "use pg over sqlite" in cp.description
        assert cp.plan == "concurrent writes matter"  # from rationale

    @pytest.mark.asyncio
    async def test_rejects_non_decision(self, tmp_path: Path) -> None:
        tickets, threads, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        note = Note(ticket_id=ticket_id, author="dev", text="just a note")
        nid = await threads.post(note)

        with pytest.raises(CheckpointError, match="not a decision"):
            await handle_checkpoint_decision(
                tickets=tickets,
                threads=threads,
                checkpoints=checkpoints,
                sender="dev",
                phase_name="implement",
                args={"decision_id": nid},
            )

    @pytest.mark.asyncio
    async def test_missing_decision_raises(self, tmp_path: Path) -> None:
        tickets, threads, checkpoints, _, _ = await _make_stores(tmp_path)
        with pytest.raises(CheckpointError, match="not found"):
            await handle_checkpoint_decision(
                tickets=tickets,
                threads=threads,
                checkpoints=checkpoints,
                sender="dev",
                phase_name="implement",
                args={"decision_id": "nonexistent"},
            )


class TestCheckpointDeferred:
    @pytest.mark.asyncio
    async def test_appends_single_item(self, tmp_path: Path) -> None:
        tickets, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        result = await handle_checkpoint_deferred(
            tickets=tickets,
            checkpoints=checkpoints,
            sender="dev",
            phase_name="implement",
            args={
                "ticket_id": ticket_id,
                "item": "refactor error path",
                "reason": "too invasive now",
            },
        )
        assert result["item"] == "refactor error path"
        cp = await checkpoints.get(result["checkpoint_id"])
        assert cp.trigger == "agent_deferred"
        assert cp.deferred[0].item == "refactor error path"
        assert cp.deferred[0].reason == "too invasive now"
        assert cp.deferred[0].status == "open"

    @pytest.mark.asyncio
    async def test_empty_item_rejected(self, tmp_path: Path) -> None:
        tickets, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        with pytest.raises(CheckpointError, match="non-empty"):
            await handle_checkpoint_deferred(
                tickets=tickets,
                checkpoints=checkpoints,
                sender="dev",
                phase_name="implement",
                args={"ticket_id": ticket_id, "item": "   "},
            )


class TestCheckpointPromoteDeferred:
    """Phase 5 Task J — evaluator promotes a deferred item to a child ticket.

    Canonical state for the DeferredItem lives on the authoring
    Checkpoint. Promotion updates that record's embedded item
    (``status="promoted"``, ``promoted_ticket_id=<new>``) and creates
    a child ticket with ``parent_id=<current ticket>``. Handoff copies
    are snapshots and stay untouched — ``deferred_items_open`` only
    surfaces open items so future handoffs won't re-offer a promoted
    one.
    """

    @pytest.mark.asyncio
    async def test_promotes_to_child_ticket(self, tmp_path: Path) -> None:
        tickets, threads, checkpoints, bus, parent_id = await _make_stores(tmp_path)
        item = DeferredItem(item="extract helper", reason="scope creep")
        cp = Checkpoint(
            ticket_id=parent_id,
            phase="implement",
            author="dev",
            trigger="agent_deferred",
            deferred=[item],
        )
        cid = await checkpoints.post(cp)

        result = await handle_checkpoint_promote_deferred(
            tickets=tickets,
            checkpoints=checkpoints,
            bus=bus,
            sender="review",
            phase_name="review",
            args={
                "ticket_id": parent_id,
                "deferred_item_id": item.id,
                "title": "Extract helper from implement phase",
                "work_type": "feature",
            },
            project_path=tmp_path,
        )

        child_id = result["ticket_id"]
        assert result["deferred_item_id"] == item.id
        child = await tickets.get(child_id)
        assert child is not None
        assert child.parent_id == parent_id
        assert child.title == "Extract helper from implement phase"
        assert child.work_type == WorkType.FEATURE

        reloaded = await checkpoints.get(cid)
        assert len(reloaded.deferred) == 1
        assert reloaded.deferred[0].status == "promoted"
        assert reloaded.deferred[0].promoted_ticket_id == child_id

    @pytest.mark.asyncio
    async def test_default_title_is_item_text(self, tmp_path: Path) -> None:
        tickets, _, checkpoints, bus, parent_id = await _make_stores(tmp_path)
        item = DeferredItem(item="rename columns")
        cp = Checkpoint(
            ticket_id=parent_id,
            phase="implement",
            author="dev",
            trigger="agent_deferred",
            deferred=[item],
        )
        await checkpoints.post(cp)

        result = await handle_checkpoint_promote_deferred(
            tickets=tickets,
            checkpoints=checkpoints,
            bus=bus,
            sender="review",
            phase_name="review",
            args={
                "ticket_id": parent_id,
                "deferred_item_id": item.id,
            },
            project_path=tmp_path,
        )
        child = await tickets.get(result["ticket_id"])
        assert child.title == "rename columns"
        # Default work_type is feature per the plan.
        assert child.work_type == WorkType.FEATURE

    @pytest.mark.asyncio
    async def test_idempotent_for_already_promoted(self, tmp_path: Path) -> None:
        """Second promote returns the existing child id, no new ticket."""
        tickets, _, checkpoints, bus, parent_id = await _make_stores(tmp_path)
        item = DeferredItem(item="again")
        cp = Checkpoint(
            ticket_id=parent_id,
            phase="implement",
            author="dev",
            trigger="agent_deferred",
            deferred=[item],
        )
        await checkpoints.post(cp)

        first = await handle_checkpoint_promote_deferred(
            tickets=tickets,
            checkpoints=checkpoints,
            bus=bus,
            sender="review",
            phase_name="review",
            args={
                "ticket_id": parent_id,
                "deferred_item_id": item.id,
            },
            project_path=tmp_path,
        )
        before_count = len(await tickets.list_all())

        second = await handle_checkpoint_promote_deferred(
            tickets=tickets,
            checkpoints=checkpoints,
            bus=bus,
            sender="review",
            phase_name="review",
            args={
                "ticket_id": parent_id,
                "deferred_item_id": item.id,
                # Differing title should NOT rename the child on re-promote.
                "title": "different",
            },
            project_path=tmp_path,
        )
        assert first["ticket_id"] == second["ticket_id"]
        after_count = len(await tickets.list_all())
        assert after_count == before_count, "re-promote must not create another ticket"

    @pytest.mark.asyncio
    async def test_unknown_deferred_item_raises(self, tmp_path: Path) -> None:
        tickets, _, checkpoints, bus, parent_id = await _make_stores(tmp_path)
        with pytest.raises(CheckpointError, match="deferred item"):
            await handle_checkpoint_promote_deferred(
                tickets=tickets,
                checkpoints=checkpoints,
                bus=bus,
                sender="review",
                phase_name="review",
                args={
                    "ticket_id": parent_id,
                    "deferred_item_id": "no-such-id",
                },
                project_path=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_finds_item_in_historical_checkpoint(self, tmp_path: Path) -> None:
        """Evaluator can still promote after the phase is marked historical.

        mark_phase_historical runs on handoff-accept. A promote call after
        that point must still locate the item.
        """
        tickets, _, checkpoints, bus, parent_id = await _make_stores(tmp_path)
        item = DeferredItem(item="late promote")
        cp = Checkpoint(
            ticket_id=parent_id,
            phase="implement",
            author="dev",
            trigger="agent_deferred",
            deferred=[item],
        )
        await checkpoints.post(cp)
        await checkpoints.mark_phase_historical(parent_id, "implement")

        result = await handle_checkpoint_promote_deferred(
            tickets=tickets,
            checkpoints=checkpoints,
            bus=bus,
            sender="review",
            phase_name="review",
            args={
                "ticket_id": parent_id,
                "deferred_item_id": item.id,
            },
            project_path=tmp_path,
        )
        child = await tickets.get(result["ticket_id"])
        assert child is not None
        assert child.parent_id == parent_id


# ---- harness hooks --------------------------------------------------------


class TestHarnessHooks:
    @pytest.mark.asyncio
    async def test_auto_commit_hook(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        cid = await record_auto_commit_checkpoint(
            checkpoints=checkpoints,
            ticket_id=ticket_id,
            phase_name="implement",
            author="dev",
            commit_sha="abcdef1234567",
            message="add migration",
        )
        cp = await checkpoints.get(cid)
        assert cp.trigger == "auto_commit"
        assert "abcdef1" in cp.description
        assert cp.completed == ["add migration"]

    @pytest.mark.asyncio
    async def test_auto_test_pass(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        cid = await record_auto_test_checkpoint(
            checkpoints=checkpoints,
            ticket_id=ticket_id,
            phase_name="implement",
            author="dev",
            passed=True,
            summary="ruff clean",
        )
        cp = await checkpoints.get(cid)
        assert cp.trigger == "auto_test"
        assert cp.position == "tests green"
        assert cp.open_questions == []

    @pytest.mark.asyncio
    async def test_auto_test_fail_captures_questions(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        cid = await record_auto_test_checkpoint(
            checkpoints=checkpoints,
            ticket_id=ticket_id,
            phase_name="implement",
            author="dev",
            passed=False,
            summary="2 lint errors",
            open_questions=["E501 too long", "F401 unused"],
        )
        cp = await checkpoints.get(cid)
        assert cp.position == "tests red"
        assert cp.open_questions == ["E501 too long", "F401 unused"]

    @pytest.mark.asyncio
    async def test_auto_pre_handoff_carries_deferred(self, tmp_path: Path) -> None:
        _, _, checkpoints, _, ticket_id = await _make_stores(tmp_path)
        deferred = [DeferredItem(item="revisit later")]
        cid = await record_auto_pre_handoff_checkpoint(
            checkpoints=checkpoints,
            ticket_id=ticket_id,
            phase_name="implement",
            author="dev",
            outputs=["src/foo.py"],
            summary="ready for review",
            deferred=deferred,
        )
        cp = await checkpoints.get(cid)
        assert cp.trigger == "auto_pre_handoff"
        assert cp.deferred[0].item == "revisit later"
        assert cp.completed == ["src/foo.py"]


# ---- integration: commit_progress -----------------------------------------


class TestCommitProgressHooks:
    @pytest.mark.asyncio
    async def test_commit_records_test_and_commit(self, tmp_path: Path) -> None:
        from jig.ticket_mcp import handle_commit_progress

        tickets, threads, checkpoints, bus, ticket_id = await _make_stores(tmp_path)

        async def fake_commit(worktree: Path, message: str) -> str:
            return "aabbccdd1122"

        with patch("jig.ticket_mcp.commit_worktree", side_effect=fake_commit):
            result = await handle_commit_progress(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                worktree_path=tmp_path,
                args={"ticket_id": ticket_id, "message": "impl foo"},
                checkpoints=checkpoints,
                phase_name="implement",
            )
        assert result["sha"] == "aabbccdd1122"

        cps = await checkpoints.for_ticket(ticket_id)
        triggers = [c.trigger for c in cps]
        # Ordering is append-order: auto_test then auto_commit.
        assert triggers == ["auto_test", "auto_commit"]

    @pytest.mark.asyncio
    async def test_commit_lint_error_still_records_test_checkpoint(
        self, tmp_path: Path
    ) -> None:
        from jig.ticket_mcp import handle_commit_progress
        from jig.worktree import LintError

        tickets, threads, checkpoints, bus, ticket_id = await _make_stores(tmp_path)

        async def lint_boom(worktree: Path, message: str) -> str:
            raise LintError(["E501 too long", "F401 unused"])

        with patch("jig.ticket_mcp.commit_worktree", side_effect=lint_boom):
            result = await handle_commit_progress(
                tickets=tickets,
                threads=threads,
                bus=bus,
                sender="dev",
                worktree_path=tmp_path,
                args={"ticket_id": ticket_id, "message": "impl foo"},
                checkpoints=checkpoints,
                phase_name="implement",
            )
        assert result["success"] is False
        cps = await checkpoints.for_ticket(ticket_id)
        assert len(cps) == 1
        assert cps[0].trigger == "auto_test"
        assert cps[0].position == "tests red"
        assert cps[0].open_questions == ["E501 too long", "F401 unused"]


# ---- integration: thread_handoff ------------------------------------------


class TestHandoffDeferredItemsFromCheckpoints:
    @pytest.mark.asyncio
    async def test_handoff_auto_merges_open_items(self, tmp_path: Path) -> None:
        tickets, threads, checkpoints, bus, ticket_id = await _make_stores(tmp_path)
        # Two agent-deferred items from the phase so far.
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_deferred",
                deferred=[DeferredItem(item="rename columns", reason="later")],
            )
        )
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_deferred",
                deferred=[DeferredItem(item="finish docs")],
            )
        )

        result = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": ["src/foo.py"],
                "summary": "ready",
                # Caller also supplies one of their own.
                "deferred_items": ["explicit one"],
            },
            checkpoints=checkpoints,
        )
        handoff = await threads.get(result["handoff_id"])
        names = [d.item for d in handoff.deferred_items]
        # Explicit first, then checkpoint-derived order.
        assert names == ["explicit one", "rename columns", "finish docs"]

    @pytest.mark.asyncio
    async def test_handoff_dedupes_explicit_against_checkpoint(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, checkpoints, bus, ticket_id = await _make_stores(tmp_path)
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_deferred",
                deferred=[DeferredItem(item="dup me", reason="meh")],
            )
        )
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
                    {"item": "dup me", "reason": "meh"},
                ],
            },
            checkpoints=checkpoints,
        )
        handoff = await threads.get(result["handoff_id"])
        assert [d.item for d in handoff.deferred_items] == ["dup me"]

    @pytest.mark.asyncio
    async def test_handoff_writes_pre_handoff_checkpoint(self, tmp_path: Path) -> None:
        tickets, threads, checkpoints, bus, ticket_id = await _make_stores(tmp_path)
        await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": ["src/foo.py"],
                "summary": "ready for review",
            },
            checkpoints=checkpoints,
        )
        cps = await checkpoints.for_ticket(ticket_id)
        assert any(c.trigger == "auto_pre_handoff" for c in cps)


class TestHandoffAcceptPrunesCheckpoints:
    @pytest.mark.asyncio
    async def test_accept_marks_phase_checkpoints_historical(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, checkpoints, bus, ticket_id = await _make_stores(tmp_path)
        save_workflow(
            tmp_path,
            WorkflowConfig(
                name="default",
                phases=[
                    PhaseConfig(name="implement", role="dev"),
                    PhaseConfig(name="review", role="reviewer"),
                ],
            ),
        )
        # Some in-flight checkpoints in the implement phase.
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
                description="step 1",
            )
        )
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
                description="step 2",
            )
        )

        result = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
            checkpoints=checkpoints,
        )
        # Pre-handoff checkpoint also lands; count all implement-phase.
        before = await checkpoints.for_phase(ticket_id, "implement")
        assert len(before) >= 3

        await handle_thread_accept_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",  # next phase's role = evaluator
            args={"handoff_id": result["handoff_id"]},
            project_path=tmp_path,
            checkpoints=checkpoints,
        )

        after_active = await checkpoints.for_phase(ticket_id, "implement")
        after_all = await checkpoints.for_phase(
            ticket_id, "implement", include_historical=True
        )
        assert after_active == []
        assert len(after_all) == len(before)
        assert all(c.historical for c in after_all)

    @pytest.mark.asyncio
    async def test_reject_leaves_checkpoints_active(self, tmp_path: Path) -> None:
        tickets, threads, checkpoints, bus, ticket_id = await _make_stores(tmp_path)
        save_workflow(
            tmp_path,
            WorkflowConfig(
                name="default",
                phases=[
                    PhaseConfig(name="implement", role="dev"),
                    PhaseConfig(name="review", role="reviewer"),
                ],
            ),
        )
        await checkpoints.post(
            Checkpoint(
                ticket_id=ticket_id,
                phase="implement",
                author="dev",
                trigger="agent_milestone",
                description="step 1",
            )
        )
        result = await handle_thread_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            args={
                "ticket_id": ticket_id,
                "phase": "implement",
                "outputs": [],
            },
            checkpoints=checkpoints,
        )
        await handle_thread_reject_handoff(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="reviewer",
            args={
                "handoff_id": result["handoff_id"],
                "reason": "missing docs",
            },
            project_path=tmp_path,
            checkpoints=checkpoints,
        )
        active = await checkpoints.for_phase(ticket_id, "implement")
        # Still live — retry picks up from here.
        assert len(active) >= 2
        assert not any(c.historical for c in active)
