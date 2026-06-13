"""Review invocation shaping (review-severity-binary, step 4).

Discovery is front-loaded: the FIRST review round of a phase runs
``phase.review_passes`` sequential reviewer passes, with passes after
the first *informed* (they receive the findings already posted this
cycle and add coverage). Re-review rounds after a blocked fix are
single-pass and *delta-scoped*: the diff is based at the last-reviewed
commit, so a new finding on untouched code is structurally impossible.

Pins, per feature-work/review-severity-binary/plan.md step 4:

- ``review_passes: 2`` runs two sequential dispatches in the first
  cycle; pass 2 receives pass 1's findings; the gate evaluates the
  union;
- ``review_passes`` default 1 leaves first-round behavior unchanged;
- a re-review cycle passes ``base_ref`` = last-reviewed commit and runs
  exactly one pass even with ``review_passes: 2``;
- missing tracked commit (daemon restart) falls back to the full-diff
  base;
- the informed-pass and delta-scope prompt sections render.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from jig.config import Config, OrchestratorSection, save_config
from jig.models import PhaseConfig
from jig.orchestrator import Orchestrator
from jig.project import Project
from jig.prompt_builder import _delta_review_section, _informed_pass_section
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
        project=Project(id="test-shaping", name="test-shaping", path=str(root)),
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


async def _make_ticket(orch: Orchestrator, ticket_id: str = "tb-shape") -> Ticket:
    assert orch.tickets is not None
    t = Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="shaping test",
        created_by="planner-pm",
        description=TICKET_AC_PLACEHOLDER,
    )
    await orch.tickets.create(t)
    fresh = await orch.tickets.get(ticket_id)
    assert fresh is not None
    return fresh


def _phase(passes: int = 1) -> PhaseConfig:
    return PhaseConfig(
        name="review",
        role="review",
        reviewers=["reviewer-generalist"],
        review_passes=passes,
    )


def _comment(prose: str = "x" * 50, file: str = "jig/foo.py") -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType("pattern-divergence"),
        severity=Severity("notable"),
        reviewer="reviewer-generalist",
        prose=prose,
        confidence=0.7,
        file=file,
    )


class _DispatchRecorder:
    """Stub for dispatch_with_llm_spawn that records every call's kwargs."""

    def __init__(self, results: list[dict] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results = results or []

    async def __call__(self, *args: Any, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        if len(self.calls) <= len(self._results):
            return self._results[len(self.calls) - 1]
        return {}


def _patch_dispatch(
    monkeypatch: pytest.MonkeyPatch, recorder: _DispatchRecorder
) -> None:
    from jig import reviewers as reviewers_pkg
    from jig.reviewers import dispatch as dispatch_module

    monkeypatch.setattr(dispatch_module, "dispatch_with_llm_spawn", recorder)
    monkeypatch.setattr(reviewers_pkg, "dispatch_with_llm_spawn", recorder)


def _git_init_with_commit(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=path,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True
    ).stdout.strip()


class TestMultiPassFirstRound:
    async def test_two_passes_second_informed_gate_sees_union(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        recorder = _DispatchRecorder(
            results=[
                {
                    "reviewer-generalist": [
                        _comment("first pass finding " + "x" * 30, file="jig/a.py")
                    ]
                },
                {
                    "reviewer-generalist": [
                        _comment("second pass finding " + "y" * 30, file="jig/b.py")
                    ]
                },
            ]
        )
        _patch_dispatch(monkeypatch, recorder)

        result = await orch._run_review_phase_federation(
            ticket.id, ticket, tmp_path, phase=_phase(passes=2), cycle=0
        )

        assert len(recorder.calls) == 2
        # Pass 1 is blind; pass 2 is informed with pass 1's findings.
        assert recorder.calls[0]["informed_findings"] is None
        informed = recorder.calls[1]["informed_findings"]
        assert informed is not None
        assert "first pass finding" in informed["findings"][0]["prose"]
        # Gate evaluated the union: both notables were filed as issues.
        assert result.status == "success"
        assert orch.tickets is not None
        issues = [
            t
            for t in await orch.tickets.list_all()
            if "review-notable" in t.labels and t.parent_id == ticket.id
        ]
        assert len(issues) == 2

    async def test_default_single_pass_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        recorder = _DispatchRecorder()
        _patch_dispatch(monkeypatch, recorder)

        await orch._run_review_phase_federation(
            ticket.id, ticket, tmp_path, phase=_phase(passes=1), cycle=0
        )

        assert len(recorder.calls) == 1
        assert recorder.calls[0]["informed_findings"] is None
        assert recorder.calls[0]["delta_base"] is None


class TestDeltaScopedReReview:
    async def test_re_review_uses_last_reviewed_commit_single_pass(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        worktree = tmp_path / "wt"
        head = _git_init_with_commit(worktree)
        recorder = _DispatchRecorder()
        _patch_dispatch(monkeypatch, recorder)

        # First round (cycle 0): records the reviewed HEAD.
        await orch._run_review_phase_federation(
            ticket.id, ticket, worktree, phase=_phase(passes=2), cycle=0
        )
        assert orch._last_reviewed_commit[(ticket.id, "review")] == head

        # Re-review round (cycle 1): single pass even with review_passes=2,
        # diff based at the recorded commit, prompt notes the delta scope.
        recorder.calls.clear()
        await orch._run_review_phase_federation(
            ticket.id, ticket, worktree, phase=_phase(passes=2), cycle=1
        )
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["base_ref"] == head
        assert recorder.calls[0]["delta_base"] == head

    async def test_missing_tracked_commit_falls_back_to_full_diff(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Daemon restart: no tracked commit → full-diff base, no delta note."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        recorder = _DispatchRecorder()
        _patch_dispatch(monkeypatch, recorder)

        await orch._run_review_phase_federation(
            ticket.id, ticket, tmp_path, phase=_phase(passes=2), cycle=1
        )

        assert len(recorder.calls) == 1
        assert recorder.calls[0]["delta_base"] is None
        # Falls back to the project default branch, not a commit sha.
        assert recorder.calls[0]["base_ref"] == "main"


class TestPromptSections:
    def test_informed_pass_section_renders(self) -> None:
        bundle = {
            "findings": [
                {
                    "file": "src/a.py",
                    "line": 3,
                    "severity": "important",
                    "prose": "missing error path",
                }
            ]
        }
        out = _informed_pass_section(bundle)
        assert "Findings Already Posted This Round" in out
        assert "src/a.py:3" in out
        assert "add coverage" in out.lower()
        assert _informed_pass_section(None) == ""
        assert _informed_pass_section({"findings": []}) == ""

    def test_delta_review_section_renders(self) -> None:
        out = _delta_review_section("abcdef1234567890")
        assert "Re-review Scope" in out
        assert "abcdef123456" in out
        assert "do not re-review unchanged code" in out.lower()
        assert _delta_review_section(None) == ""
