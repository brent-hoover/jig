"""Notable→proposed-issue conversion (review-severity-binary, step 1).

Binary severity: notables never block, never route, and carry no ack
obligation. Each distinct notable becomes a ``proposed`` ticket — the
operator-gated issue front door — deduped by finding signature so a
re-posted notable (later cycle or daemon restart) files exactly once.

Pins, per feature-work/review-severity-binary/plan.md step 1:

- the in-workflow phase gate (`_run_review_phase_federation`) passes a
  notable-only cycle and files the issue (the post-resolve gate's
  equivalent lives in test_orchestrator_review_gate.py);
- dedup across repeated federation runs;
- the dispatch gate: a ``review-notable`` proposed ticket is never
  picked up by ``find_ready``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jig.config import Config, OrchestratorSection, save_config
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
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _seed_project(root: Path) -> None:
    cfg_dir = root / ".jig"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(
        project=Project(
            id="test-notable-issues", name="test-notable-issues", path=str(root)
        ),
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


async def _make_ticket(orch: Orchestrator, ticket_id: str = "tb-notable") -> Ticket:
    assert orch.tickets is not None
    t = Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="notable filing test",
        created_by="planner-pm",
        description=TICKET_AC_PLACEHOLDER,
    )
    await orch.tickets.create(t)
    fresh = await orch.tickets.get(ticket_id)
    assert fresh is not None
    return fresh


def _notable(
    *,
    file: str = "src/cli.py",
    prose: str = "noqa B008 applied inconsistently across sibling options",
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType("pattern-divergence"),
        severity=Severity("notable"),
        reviewer="reviewer-generalist",
        prose=prose,
        confidence=0.72,
        file=file,
        line=46,
    )


def _patch_federation(monkeypatch: pytest.MonkeyPatch, by_reviewer: dict) -> None:
    async def _stub(*args: Any, **kwargs: Any) -> dict:
        return by_reviewer

    from jig import reviewers as reviewers_pkg
    from jig.reviewers import dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "dispatch_with_llm_spawn", _stub)
    monkeypatch.setattr(reviewers_pkg, "dispatch_with_llm_spawn", _stub)


def _issues_for(tickets: list[Ticket], parent_id: str) -> list[Ticket]:
    return [
        t for t in tickets if "review-notable" in t.labels and t.parent_id == parent_id
    ]


class TestInPhaseGate:
    async def test_notable_only_cycle_passes_and_files_issue(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        _patch_federation(monkeypatch, {"reviewer-generalist": [_notable()]})

        result = await orch._run_review_phase_federation(ticket.id, ticket, tmp_path)

        # The gate passes — a notable can never block.
        assert result.status == "success"

        assert orch.tickets is not None
        issues = _issues_for(await orch.tickets.list_all(), ticket.id)
        assert len(issues) == 1
        issue = issues[0]
        assert issue.status == TicketStatus.PROPOSED
        assert issue.created_by == "review-federation"
        assert any(label.startswith("sig:") for label in issue.labels)
        # The description carries an AC section (system invariant) and
        # the finding's location for triage.
        assert "## Acceptance criteria" in issue.description
        assert "src/cli.py:46" in issue.description

    async def test_repeated_notable_files_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The RC-3/RC-4 eval pattern: the same notable re-posted on a
        later cycle dedupes by signature instead of filing again."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        _patch_federation(monkeypatch, {"reviewer-generalist": [_notable()]})

        await orch._run_review_phase_federation(ticket.id, ticket, tmp_path)
        await orch._run_review_phase_federation(ticket.id, ticket, tmp_path, cycle=1)

        assert orch.tickets is not None
        issues = _issues_for(await orch.tickets.list_all(), ticket.id)
        assert len(issues) == 1

    async def test_distinct_notables_file_separately(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        _patch_federation(
            monkeypatch,
            {
                "reviewer-generalist": [
                    _notable(file="src/a.py"),
                    _notable(file="src/b.py"),
                ]
            },
        )

        await orch._run_review_phase_federation(ticket.id, ticket, tmp_path)

        assert orch.tickets is not None
        issues = _issues_for(await orch.tickets.list_all(), ticket.id)
        assert len(issues) == 2


class TestDispatchGate:
    async def test_review_notable_proposed_ticket_is_never_ready(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The filed issue must be invisible to the scheduler until the
        operator approves it (proposed → open via the issue front door)."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        _patch_federation(monkeypatch, {"reviewer-generalist": [_notable()]})
        await orch._run_review_phase_federation(ticket.id, ticket, tmp_path)

        assert orch.tickets is not None
        issues = _issues_for(await orch.tickets.list_all(), ticket.id)
        assert len(issues) == 1

        ready_ids = {t.id for t in await orch.tickets.find_ready()}
        assert issues[0].id not in ready_ids
