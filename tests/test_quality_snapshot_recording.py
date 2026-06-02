"""Sub-issue D: dispatch_with_llm_spawn must record a QualitySnapshot once
per end-of-ticket cycle, with cell + role_versions populated for spawned
LLM reviewers. The recording is signal-only — failures degrade silently."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.code_metrics import ChangeMetrics
from jig.code_quality.taxonomy import TaxonomyHit
from jig.reviewers.dispatch import dispatch_with_llm_spawn
from jig.store.quality import QualitySnapshotStore
from tests.test_reviewers_federation_execution import (  # noqa: F401 — fixtures
    _FakeOrchestrator,
    _init_worktree,
    _ticket,
    _write_arch,
    _write_contracts,
    _write_spec,
)


@pytest.mark.asyncio
async def test_dispatch_records_quality_snapshot(tmp_path: Path, monkeypatch) -> None:
    async def fake_compute(*_a, **_k):
        return ChangeMetrics(
            max_cc=12,
            max_cc_location="m.py:f",
            ruff_findings=2,
            loc_delta=42,
            taxonomy_hits=(
                TaxonomyHit(
                    id="TAX-SEC-001",
                    category="security",
                    file="m.py",
                    line=1,
                    reviewer="reviewer-security",
                ),
                TaxonomyHit(
                    id="TAX-ERR-001",
                    category="error-handling",
                    file="m.py",
                    line=2,
                    reviewer="reviewer-error-handling",
                ),
            ),
        )

    monkeypatch.setattr("jig.code_metrics.compute_change_metrics", fake_compute)

    _write_arch(tmp_path)
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
    _init_worktree(worktree)

    orch = _FakeOrchestrator()
    await dispatch_with_llm_spawn(
        _ticket(labels=["touches-auth"]),
        tmp_path,
        orch,  # type: ignore[arg-type]
        worktree_path=worktree,
    )

    store = QualitySnapshotStore(
        tmp_path / ".jig" / "store" / "quality_snapshots.jsonl"
    )
    await store.load()
    snaps = await store.for_ticket("tb-fed")
    assert len(snaps) == 1, snaps
    s = snaps[0]
    assert s.max_cc == 12
    assert s.ruff_findings == 2
    assert s.loc_delta == 42
    assert s.taxonomy_hit_counts == {"security": 1, "error-handling": 1}
    assert (
        s.cell.get("workflow_name") is not None
    )  # ticket may have empty workflow attr
    assert s.cell.get("layer") == "mvp"
    assert s.cell.get("work_type") == "feature"
    assert "reviewer-security" in s.spawned_reviewers
    # role_versions: shipped reviewer_security.yaml exists, so its hash is present.
    assert s.role_versions.get("reviewer-security"), s.role_versions
    assert len(s.role_versions["reviewer-security"]) == 12  # short sha (12 chars)


@pytest.mark.asyncio
async def test_dispatch_records_snapshot_with_no_taxonomy_hits(
    tmp_path: Path, monkeypatch
) -> None:
    """Even when the change has no taxonomy findings, dispatch still records
    a snapshot — measurement must cover every end-of-ticket cycle, not only
    cycles where the federation found something."""

    async def fake_compute(*_a, **_k):
        return ChangeMetrics(
            max_cc=3,
            max_cc_location="x.py:g",
            ruff_findings=0,
            loc_delta=5,
            taxonomy_hits=(),
        )

    monkeypatch.setattr("jig.code_metrics.compute_change_metrics", fake_compute)

    _write_arch(tmp_path)
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
    _init_worktree(worktree)

    orch = _FakeOrchestrator()
    await dispatch_with_llm_spawn(
        _ticket(),
        tmp_path,
        orch,  # type: ignore[arg-type]
        worktree_path=worktree,
    )

    store = QualitySnapshotStore(
        tmp_path / ".jig" / "store" / "quality_snapshots.jsonl"
    )
    await store.load()
    snaps = await store.for_ticket("tb-fed")
    assert len(snaps) == 1
    s = snaps[0]
    assert s.taxonomy_hit_counts == {}
    assert s.max_cc == 3
    assert s.loc_delta == 5


@pytest.mark.asyncio
async def test_dispatch_records_snapshot_before_no_pendings_early_return(
    tmp_path: Path, monkeypatch
) -> None:
    """When ``select_reviewers_for_ticket`` returns nothing, the early-return
    must not skip snapshot recording. We force an empty selection by
    monkeypatching the selector — a real ticket can't normally trip this
    path (judgment defaults are always appended) but the code contract is
    that the recording fires regardless."""

    async def fake_compute(*_a, **_k):
        return ChangeMetrics(
            max_cc=1,
            max_cc_location="x.py:g",
            ruff_findings=0,
            loc_delta=0,
            taxonomy_hits=(),
        )

    monkeypatch.setattr("jig.code_metrics.compute_change_metrics", fake_compute)
    monkeypatch.setattr(
        "jig.reviewers.dispatch.select_reviewers_for_ticket",
        lambda *_a, **_k: [],
    )

    _write_arch(tmp_path)
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
    _init_worktree(worktree)

    orch = _FakeOrchestrator()
    await dispatch_with_llm_spawn(
        _ticket(),
        tmp_path,
        orch,  # type: ignore[arg-type]
        worktree_path=worktree,
    )

    store = QualitySnapshotStore(
        tmp_path / ".jig" / "store" / "quality_snapshots.jsonl"
    )
    await store.load()
    snaps = await store.for_ticket("tb-fed")
    assert len(snaps) == 1
    s = snaps[0]
    assert s.spawned_reviewers == ()
    assert s.role_versions == {}


@pytest.mark.asyncio
async def test_dispatch_idempotent_per_run_id_and_reviewer_set(
    tmp_path: Path, monkeypatch
) -> None:
    """Retrying a dispatch with the same cycle AND same spawned reviewer
    set must not produce duplicate rows (operator-level retry). But two
    different phases at ``cycle=0`` (e.g. ``review-tests`` then final
    ``review``) spawn different reviewer sets — both must be recorded so
    ``jig audit quality`` reflects the actual end-of-ticket metrics, not
    the test-review pre-implementation snapshot."""

    async def fake_compute(*_a, **_k):
        return ChangeMetrics(
            max_cc=5,
            max_cc_location="x.py:g",
            ruff_findings=1,
            loc_delta=10,
            taxonomy_hits=(),
        )

    monkeypatch.setattr("jig.code_metrics.compute_change_metrics", fake_compute)

    _write_arch(tmp_path)
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
    _init_worktree(worktree)

    orch = _FakeOrchestrator()

    # Retry path: same ticket + cycle + reviewer set → ONE row.
    for _ in range(2):
        await dispatch_with_llm_spawn(
            _ticket(),
            tmp_path,
            orch,  # type: ignore[arg-type]
            worktree_path=worktree,
            cycle=0,
        )

    store = QualitySnapshotStore(
        tmp_path / ".jig" / "store" / "quality_snapshots.jsonl"
    )
    await store.load()
    snaps_after_retry = await store.for_run("tb-fed.cycle0")
    assert len(snaps_after_retry) == 1, snaps_after_retry

    # Different phase path: same cycle, explicit reviewers arg narrows the
    # spawned set to a different shape → must produce a SECOND row.
    await dispatch_with_llm_spawn(
        _ticket(),
        tmp_path,
        orch,  # type: ignore[arg-type]
        worktree_path=worktree,
        cycle=0,
        reviewers=["reviewer-security"],
    )

    await store.load()
    snaps_two_phases = await store.for_run("tb-fed.cycle0")
    assert len(snaps_two_phases) == 2, snaps_two_phases
    reviewer_sets = {s.spawned_reviewers for s in snaps_two_phases}
    assert ("reviewer-security",) in reviewer_sets
