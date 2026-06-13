"""Persistence counting, observe-only (review-severity-binary, step 5).

A blocking finding's coarse persistence key (reviewer|type|file)
accumulates a survival count only when it appears in *consecutive*
blocked rounds — i.e. the dev attempted a fix and the reviewer
re-raised it. Fresh findings on a revised diff enter at zero; a key
absent from a round resets to zero. No behavior change in this step:
the round cap still fails the ticket exactly as before; counts feed
``ReviewFindingPersisted`` analytics so the step-6 SA threshold is
calibrated against real survival distributions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jig.config import Config, OrchestratorSection, save_config
from jig.models import PhaseConfig
from jig.orchestrator import Orchestrator, _persistence_key
from jig.project import Project
from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.store import MessageBus
from jig.store.tickets import TicketStore
from jig.store.threads import ThreadStore
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _seed_project(root: Path) -> None:
    cfg_dir = root / ".jig"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(
        project=Project(id="test-persist", name="test-persist", path=str(root)),
        orchestrator=OrchestratorSection(run_review_federation=True),
    )
    save_config(root, cfg)


async def _make_orch(tmp_path: Path) -> Orchestrator:
    _seed_project(tmp_path)
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(project_path=tmp_path)
    orch.tickets = TicketStore(store_dir / "tickets.jsonl")
    orch.threads = ThreadStore(store_dir / "comments.jsonl")
    orch.bus = MessageBus(store_dir / "messages.jsonl")
    await orch.tickets.load()
    await orch.threads.load()
    await orch.bus.load()
    return orch


async def _make_ticket(orch: Orchestrator, ticket_id: str = "tb-persist") -> Ticket:
    assert orch.tickets is not None
    t = Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="persistence test",
        created_by="planner-pm",
        description=TICKET_AC_PLACEHOLDER,
    )
    await orch.tickets.create(t)
    fresh = await orch.tickets.get(ticket_id)
    assert fresh is not None
    return fresh


def _phase() -> PhaseConfig:
    return PhaseConfig(name="review", role="review", reviewers=["reviewer-generalist"])


def _important(file: str, prose: str = "z" * 50) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType("pattern-divergence"),
        severity=Severity("important"),
        reviewer="reviewer-generalist",
        prose=prose,
        confidence=0.8,
        file=file,
    )


def _patch_dispatch_sequence(
    monkeypatch: pytest.MonkeyPatch, rounds: list[list[ReviewerComment]]
) -> None:
    """Each successive federation call returns the next round's comments."""
    calls = {"n": 0}

    async def _stub(*args: Any, **kwargs: Any) -> dict:
        idx = min(calls["n"], len(rounds) - 1)
        calls["n"] += 1
        return {"reviewer-generalist": rounds[idx]}

    from jig import reviewers as reviewers_pkg
    from jig.reviewers import dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "dispatch_with_llm_spawn", _stub)
    monkeypatch.setattr(reviewers_pkg, "dispatch_with_llm_spawn", _stub)


async def _run_round(orch: Orchestrator, ticket: Ticket, tmp_path: Path, cycle: int):
    return await orch._run_review_phase_federation(
        ticket.id, ticket, tmp_path, phase=_phase(), cycle=cycle
    )


class TestSurvivalCounting:
    async def test_same_key_across_rounds_increments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        finding = _important("src/a.py")
        _patch_dispatch_sequence(monkeypatch, [[finding], [finding], [finding]])
        key = _persistence_key(finding)
        phase_key = (ticket.id, "review")

        r0 = await _run_round(orch, ticket, tmp_path, cycle=0)
        assert r0.status == "blocked"
        assert orch._survival_counts[phase_key][key] == 0  # fresh

        r1 = await _run_round(orch, ticket, tmp_path, cycle=1)
        assert r1.status == "blocked"
        assert orch._survival_counts[phase_key][key] == 1  # survived one fix

        r2 = await _run_round(orch, ticket, tmp_path, cycle=2)
        assert r2.status == "blocked"
        assert orch._survival_counts[phase_key][key] == 2

    async def test_different_finding_each_round_never_accumulates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The hn-cli churn pattern: a NEW finding every round must not
        build toward escalation — each enters at zero."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        rounds = [[_important(f"src/f{i}.py")] for i in range(3)]
        _patch_dispatch_sequence(monkeypatch, rounds)
        phase_key = (ticket.id, "review")

        for cycle in range(3):
            await _run_round(orch, ticket, tmp_path, cycle=cycle)

        assert all(v == 0 for v in orch._survival_counts[phase_key].values())

    async def test_absent_key_resets_to_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Flapping: present, present (count 1), absent (reset), present
        again — resumes from zero, not one."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        a = _important("src/a.py")
        b = _important("src/b.py")
        _patch_dispatch_sequence(monkeypatch, [[a], [a], [b], [a]])
        key = _persistence_key(a)
        phase_key = (ticket.id, "review")

        await _run_round(orch, ticket, tmp_path, cycle=0)
        await _run_round(orch, ticket, tmp_path, cycle=1)
        assert orch._survival_counts[phase_key][key] == 1

        await _run_round(orch, ticket, tmp_path, cycle=2)  # a absent
        assert orch._survival_counts[phase_key][key] == 0

        await _run_round(orch, ticket, tmp_path, cycle=3)  # a returns: fresh
        assert orch._survival_counts[phase_key][key] == 0

    async def test_gate_pass_clears_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        a = _important("src/a.py")
        _patch_dispatch_sequence(monkeypatch, [[a], []])
        phase_key = (ticket.id, "review")

        await _run_round(orch, ticket, tmp_path, cycle=0)
        assert phase_key in orch._survival_counts

        r = await _run_round(orch, ticket, tmp_path, cycle=1)
        assert r.status == "success"
        assert phase_key not in orch._survival_counts
        assert phase_key not in orch._prev_blocking_keys

    async def test_no_behavior_change_blocked_status_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Observe-only: persistence never changes the gate verdict."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        finding = _important("src/a.py")
        _patch_dispatch_sequence(monkeypatch, [[finding]] * 6)

        for cycle in range(6):
            result = await _run_round(orch, ticket, tmp_path, cycle=cycle)
            assert result.status == "blocked"


class TestAnalytics:
    async def test_persisted_event_emitted_with_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        finding = _important("src/a.py")
        _patch_dispatch_sequence(monkeypatch, [[finding], [finding]])

        emitted: list = []

        class _Emitter:
            def emit_nowait(self, event) -> None:
                emitted.append(event)

        orch._analytics_emitter = _Emitter()  # type: ignore[assignment]

        await _run_round(orch, ticket, tmp_path, cycle=0)
        persisted = [e for e in emitted if e.kind == "review_finding_persisted"]
        assert not persisted  # fresh finding: no event

        await _run_round(orch, ticket, tmp_path, cycle=1)
        persisted = [e for e in emitted if e.kind == "review_finding_persisted"]
        assert len(persisted) == 1
        assert persisted[0].survival_count == 1
        assert persisted[0].persistence_key == _persistence_key(finding)
        assert persisted[0].cycle == 1
