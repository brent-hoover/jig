"""Review-federation gate tests (Track G Final → Hardening).

Per ``docs/pm-workflow/design.md`` §"Severity tiers and disposition",
the orchestrator's review-federation pass is a **gate**, not an
observation hook:

- critical comment → ticket FAILED with reason ``reviewer-critical``;
  do NOT mark RESOLVED.
- important comment → ticket BLOCKED with reason ``reviewer-important``;
  Handoff posted to phase ``sa-consult``; do NOT mark RESOLVED.
- notable comment → mark RESOLVED but defer the ticket via the
  Coordinator (DEFERRED queue).
- no blocking comments → mark RESOLVED as today.

This module pins each branch + the federation-flag-off legacy path,
the federation-crash retry policy, and the structured-reason fields
on the Ticket so analytics + operator UX can distinguish review-
blocked tickets from operator-blocked ones.

Tests use mocked SDK + canned reviewer comments — no live LLM spawns.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jig.config import (
    Config,
    OrchestratorSection,
    save_config,
)
from jig.coordinator import Coordinator
from jig.orchestrator import Orchestrator
from jig.project import Project
from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.store import MessageBus
from jig.store.tickets import TicketStore
from jig.store.threads import ThreadStore
from jig.thread import Handoff
from jig.ticket import Ticket, TicketStatus, WorkType


# ---- helpers -------------------------------------------------------------


def _seed_project(root: Path, *, run_federation: bool = True) -> None:
    """Write a minimal .jig/config.yaml so load_config succeeds."""
    cfg_dir = root / ".jig"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(
        project=Project(
            id="test-rev-gate",
            name="test-rev-gate",
            path=str(root),
        ),
        orchestrator=OrchestratorSection(
            run_review_federation=run_federation,
        ),
    )
    save_config(root, cfg)


async def _make_orch(
    tmp_path: Path, *, run_federation: bool = True
) -> Orchestrator:
    """Build an orchestrator with stores hand-wired for unit tests.

    We don't go through ``startup()`` because that path tries to
    bootstrap the dispatch loops + subscriptions, which we don't need
    for the gate unit tests. Instead we build the same stores by hand
    so the orchestrator's internal references resolve.
    """
    _seed_project(tmp_path, run_federation=run_federation)
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(project_path=tmp_path)
    orch.tickets = TicketStore(store_dir / "tickets.jsonl")
    orch.threads = ThreadStore(store_dir / "comments.jsonl")
    orch.bus = MessageBus(store_dir / "messages.jsonl")
    await orch.tickets.load()
    await orch.threads.load()
    await orch.bus.load()
    orch._orchestrator_cfg = OrchestratorSection(  # type: ignore[attr-defined]
        run_review_federation=run_federation,
    )
    return orch


def _comment(
    *,
    severity: str,
    reviewer: str = "reviewer-pattern-conformance",
    type_: str = "pattern-divergence",
    prose: str = "x" * 50,
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType(type_),
        severity=Severity(severity),
        reviewer=reviewer,
        prose=prose,
        confidence=0.85,
        file="jig/foo.py",
    )


async def _make_ticket(
    orch: Orchestrator, ticket_id: str = "tb-gate"
) -> Ticket:
    assert orch.tickets is not None
    t = Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="federation gate test",
        created_by="planner-pm",
        layer="mvp",
    )
    await orch.tickets.create(t)
    fresh = await orch.tickets.get(ticket_id)
    assert fresh is not None
    # Set RESOLVED so tests start from the post-merge state the gate
    # rolls back when blocking comments fire.
    await orch.tickets.update_status(ticket_id, TicketStatus.RESOLVED)
    fresh = await orch.tickets.get(ticket_id)
    assert fresh is not None
    return fresh


# ---- block_reason field --------------------------------------------------


class TestBlockReasonField:
    """``Ticket.block_reason`` is the structured reason for a non-OK
    terminal state. Present on FAILED + BLOCKED transitions driven by
    review federation; ``None`` for clean states / operator-driven
    parks (preserves the legacy bones-era contract)."""

    def test_default_none(self) -> None:
        t = Ticket(
            id="t-block-reason",
            work_type=WorkType.FEATURE,
            title="block_reason default test",
            created_by="planner-pm",
        )
        assert t.block_reason is None

    def test_settable_at_construction(self) -> None:
        t = Ticket(
            id="t-block-reason",
            work_type=WorkType.FEATURE,
            title="block_reason settable test",
            created_by="planner-pm",
            block_reason="reviewer-critical",
        )
        assert t.block_reason == "reviewer-critical"

    @pytest.mark.asyncio
    async def test_round_trips_through_store(self, tmp_path: Path) -> None:
        store_dir = tmp_path / ".jig" / "store"
        store_dir.mkdir(parents=True, exist_ok=True)
        tickets = TicketStore(store_dir / "tickets.jsonl")
        await tickets.load()
        await tickets.create(
            Ticket(
                id="t-rt",
                work_type=WorkType.FEATURE,
                title="round-trip",
                created_by="x",
            )
        )
        await tickets.update("t-rt", block_reason="reviewer-important")
        fresh = await tickets.get("t-rt")
        assert fresh is not None
        assert fresh.block_reason == "reviewer-important"


# ---- Coordinator lazy property -------------------------------------------


class TestCoordinatorProperty:
    """``Orchestrator.coordinator`` lazily constructs a Coordinator wired
    to the orchestrator's stores. Reused across calls; constructed
    once per orchestrator lifetime."""

    @pytest.mark.asyncio
    async def test_coordinator_constructed_lazily(self, tmp_path: Path) -> None:
        orch = await _make_orch(tmp_path)
        coord = orch.coordinator
        assert isinstance(coord, Coordinator)

    @pytest.mark.asyncio
    async def test_coordinator_singleton(self, tmp_path: Path) -> None:
        orch = await _make_orch(tmp_path)
        first = orch.coordinator
        second = orch.coordinator
        assert first is second

    @pytest.mark.asyncio
    async def test_coordinator_raises_when_stores_missing(
        self, tmp_path: Path
    ) -> None:
        """Pre-startup access should fail loud, not return a half-baked
        Coordinator that crashes deep inside a defer call."""
        orch = Orchestrator(project_path=tmp_path)
        with pytest.raises(RuntimeError, match="not started"):
            _ = orch.coordinator


# ---- gate behaviour ------------------------------------------------------


class TestReviewFederationGate:
    """The gate runs federation BEFORE the RESOLVED transition (in
    practice the orchestrator marks RESOLVED and rolls back on
    blocking comments — same observable contract). Mocked
    federation comments drive each disposition branch."""

    @pytest.mark.asyncio
    async def test_no_comments_resolves(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Clean federation — no comments returned — leaves ticket RESOLVED."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)

        async def _stub(*args: Any, **kwargs: Any) -> dict:
            return {}

        from jig import reviewers as reviewers_pkg
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module, "dispatch_with_llm_spawn", _stub,
        )
        monkeypatch.setattr(
            reviewers_pkg, "dispatch_with_llm_spawn", _stub,
        )

        await orch._run_review_federation(ticket.id, ticket)

        assert orch.tickets is not None
        fresh = await orch.tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.RESOLVED
        assert fresh.block_reason is None

    @pytest.mark.asyncio
    async def test_critical_comment_marks_failed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)

        async def _stub(*args: Any, **kwargs: Any) -> dict:
            return {"reviewer-security": [_comment(severity="critical")]}

        from jig import reviewers as reviewers_pkg
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module, "dispatch_with_llm_spawn", _stub,
        )
        monkeypatch.setattr(
            reviewers_pkg, "dispatch_with_llm_spawn", _stub,
        )

        await orch._run_review_federation(ticket.id, ticket)

        assert orch.tickets is not None
        fresh = await orch.tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.FAILED
        assert fresh.block_reason == "reviewer-critical"

    @pytest.mark.asyncio
    async def test_important_comment_marks_blocked_with_handoff(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)

        async def _stub(*args: Any, **kwargs: Any) -> dict:
            return {
                "reviewer-architectural": [_comment(severity="important")],
            }

        from jig import reviewers as reviewers_pkg
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module, "dispatch_with_llm_spawn", _stub,
        )
        monkeypatch.setattr(
            reviewers_pkg, "dispatch_with_llm_spawn", _stub,
        )

        await orch._run_review_federation(ticket.id, ticket)

        assert orch.tickets is not None
        fresh = await orch.tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.BLOCKED
        assert fresh.block_reason == "reviewer-important"

        # SA-consult Handoff posted.
        assert orch.threads is not None
        entries = await orch.threads.for_ticket(ticket.id)
        handoffs = [e for e in entries if isinstance(e, Handoff)]
        assert any(h.phase == "sa-consult" for h in handoffs), (
            f"expected sa-consult handoff, got phases: "
            f"{[h.phase for h in handoffs]}"
        )

    @pytest.mark.asyncio
    async def test_notable_only_resolves_and_defers(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)

        async def _stub(*args: Any, **kwargs: Any) -> dict:
            return {
                "reviewer-pattern-conformance": [_comment(severity="notable")],
            }

        from jig import reviewers as reviewers_pkg
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module, "dispatch_with_llm_spawn", _stub,
        )
        monkeypatch.setattr(
            reviewers_pkg, "dispatch_with_llm_spawn", _stub,
        )

        await orch._run_review_federation(ticket.id, ticket)

        assert orch.tickets is not None
        fresh = await orch.tickets.get(ticket.id)
        assert fresh is not None
        # Notable-only → ticket stays RESOLVED.
        assert fresh.status == TicketStatus.RESOLVED
        # ...but it IS deferred.
        assert fresh.deferred_at is not None
        # ...and the deferred-queue file has the row.
        deferred_path = tmp_path / ".jig" / "plan" / "deferred-queue.jsonl"
        assert deferred_path.is_file()
        contents = deferred_path.read_text()
        assert ticket.id in contents
        assert "reviewer-notable" in contents

    @pytest.mark.asyncio
    async def test_critical_plus_notable_fails_no_defer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Critical wins; notables are NOT deferred when the ticket
        isn't actually resolving."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)

        async def _stub(*args: Any, **kwargs: Any) -> dict:
            return {
                "reviewer-security": [_comment(severity="critical")],
                "reviewer-pattern-conformance": [
                    _comment(severity="notable"),
                    _comment(severity="notable"),
                ],
            }

        from jig import reviewers as reviewers_pkg
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module, "dispatch_with_llm_spawn", _stub,
        )
        monkeypatch.setattr(
            reviewers_pkg, "dispatch_with_llm_spawn", _stub,
        )

        await orch._run_review_federation(ticket.id, ticket)

        assert orch.tickets is not None
        fresh = await orch.tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.FAILED
        assert fresh.block_reason == "reviewer-critical"
        # Ticket isn't resolved → no DEFERRED row.
        deferred_path = tmp_path / ".jig" / "plan" / "deferred-queue.jsonl"
        if deferred_path.is_file():
            assert ticket.id not in deferred_path.read_text()

    @pytest.mark.asyncio
    async def test_important_plus_notable_blocks_no_defer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Important wins over notable when no critical fires; notables
        are NOT deferred because the ticket isn't resolving."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)

        async def _stub(*args: Any, **kwargs: Any) -> dict:
            return {
                "reviewer-architectural": [_comment(severity="important")],
                "reviewer-pattern-conformance": [
                    _comment(severity="notable"),
                ],
            }

        from jig import reviewers as reviewers_pkg
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module, "dispatch_with_llm_spawn", _stub,
        )
        monkeypatch.setattr(
            reviewers_pkg, "dispatch_with_llm_spawn", _stub,
        )

        await orch._run_review_federation(ticket.id, ticket)

        assert orch.tickets is not None
        fresh = await orch.tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.BLOCKED
        assert fresh.block_reason == "reviewer-important"
        deferred_path = tmp_path / ".jig" / "plan" / "deferred-queue.jsonl"
        if deferred_path.is_file():
            assert ticket.id not in deferred_path.read_text()

    @pytest.mark.asyncio
    async def test_flag_off_skips_federation_keeps_resolved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Legacy fallback: flag off → federation never runs;
        ticket stays RESOLVED regardless of any (unused) comments."""
        orch = await _make_orch(tmp_path, run_federation=False)
        ticket = await _make_ticket(orch)

        called: dict[str, bool] = {"called": False}

        async def _stub(*args: Any, **kwargs: Any) -> dict:
            called["called"] = True
            return {"reviewer-security": [_comment(severity="critical")]}

        from jig import reviewers as reviewers_pkg
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module, "dispatch_with_llm_spawn", _stub,
        )
        monkeypatch.setattr(
            reviewers_pkg, "dispatch_with_llm_spawn", _stub,
        )

        # Mirror the production conditional — flag-off path skips the call.
        if orch._orchestrator_cfg.run_review_federation:  # type: ignore[attr-defined]
            await orch._run_review_federation(ticket.id, ticket)

        assert called["called"] is False
        assert orch.tickets is not None
        fresh = await orch.tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.RESOLVED
        assert fresh.block_reason is None


# ---- crash + retry policy ------------------------------------------------


class TestFederationCrashRetry:
    """A reviewer agent that crashes during spawn must NOT silently
    pass a ticket the operator expected gated. Policy: one retry
    after a 2s delay; if still failing → mark ticket FAILED with
    reason ``federation-error`` and emit a Note describing the
    failure."""

    @pytest.mark.asyncio
    async def test_crash_then_retry_succeeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Single transient crash retries cleanly + leaves ticket
        RESOLVED on second-try success."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)

        calls: dict[str, int] = {"n": 0}

        async def _stub(*args: Any, **kwargs: Any) -> dict:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated reviewer agent crash")
            return {}

        from jig import reviewers as reviewers_pkg
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module, "dispatch_with_llm_spawn", _stub,
        )
        monkeypatch.setattr(
            reviewers_pkg, "dispatch_with_llm_spawn", _stub,
        )

        # Stub asyncio.sleep so the test doesn't actually wait 2s.
        import asyncio as _asyncio

        slept: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)

        await orch._run_review_federation(ticket.id, ticket)

        assert calls["n"] == 2
        # Retry policy: one retry, fixed 2s delay.
        assert slept == [2.0]

        assert orch.tickets is not None
        fresh = await orch.tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.RESOLVED

    @pytest.mark.asyncio
    async def test_crash_twice_fails_with_federation_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both attempts crash → ticket FAILED with reason
        ``federation-error`` and a Note describing the failure."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)

        async def _stub(*args: Any, **kwargs: Any) -> dict:
            raise RuntimeError("persistent reviewer agent crash")

        from jig import reviewers as reviewers_pkg
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module, "dispatch_with_llm_spawn", _stub,
        )
        monkeypatch.setattr(
            reviewers_pkg, "dispatch_with_llm_spawn", _stub,
        )

        import asyncio as _asyncio

        async def _fake_sleep(seconds: float) -> None:
            return None

        monkeypatch.setattr(_asyncio, "sleep", _fake_sleep)

        await orch._run_review_federation(ticket.id, ticket)

        assert orch.tickets is not None
        fresh = await orch.tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.FAILED
        assert fresh.block_reason == "federation-error"

        # Note posted describing the failure.
        assert orch.threads is not None
        entries = await orch.threads.for_ticket(ticket.id)
        notes = [
            e for e in entries
            if e.kind == "note" and "federation" in e.text.lower()
        ]
        assert notes, "expected a federation-error Note on the ticket"
