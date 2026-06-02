from __future__ import annotations

from pathlib import Path

import pytest

from jig.store.quality import QualitySnapshot, QualitySnapshotStore


@pytest.mark.asyncio
async def test_snapshot_roundtrip(tmp_path: Path) -> None:
    store = QualitySnapshotStore(tmp_path / "q.jsonl")
    await store.load()
    snap = QualitySnapshot(
        ticket_id="t1",
        run_id="r1",
        max_cc=12,
        ruff_findings=3,
        loc_delta=42,
        taxonomy_hit_counts={"security": 1, "error-handling": 2},
        cell={"workflow_name": "default", "layer": "mvp", "work_type": "feature"},
        spawned_reviewers=("reviewer-security",),
        role_versions={"reviewer-security": "abc123def456"},
    )
    await store.append(snap)

    again = QualitySnapshotStore(tmp_path / "q.jsonl")
    await again.load()
    rows = await again.for_ticket("t1")
    assert len(rows) == 1
    r = rows[0]
    assert r.max_cc == 12
    assert r.ruff_findings == 3
    assert r.loc_delta == 42
    assert dict(r.taxonomy_hit_counts)["error-handling"] == 2
    assert dict(r.cell)["workflow_name"] == "default"
    assert r.spawned_reviewers == ("reviewer-security",)
    assert dict(r.role_versions) == {"reviewer-security": "abc123def456"}


def test_snapshot_is_deeply_immutable() -> None:
    """``frozen=True`` alone only blocks attribute reassignment; the
    nested-map fields are stored as sorted tuples-of-pairs so in-place
    mutation is rejected at runtime. Measurement records must not be
    silently mutable post-load — that would produce misleading audit
    output. Regression test for PR #127 roborev job #273."""
    snap = QualitySnapshot(
        ticket_id="t1",
        run_id="r1",
        max_cc=1,
        ruff_findings=0,
        loc_delta=0,
        taxonomy_hit_counts={"security": 1},
        cell={"layer": "mvp"},
        role_versions={"reviewer-security": "abc123def456"},
    )

    # Attribute reassignment blocked by frozen=True.
    with pytest.raises((TypeError, ValueError)):
        snap.max_cc = 99  # type: ignore[misc]

    # Tuple-of-pairs nested fields — no __setitem__ available.
    with pytest.raises(TypeError):
        snap.taxonomy_hit_counts[0] = ("evicted", 0)  # type: ignore[index]
    with pytest.raises(TypeError):
        snap.cell[0] = ("evicted", "evicted")  # type: ignore[index]
    with pytest.raises(TypeError):
        snap.role_versions[0] = ("evicted", "0")  # type: ignore[index]


@pytest.mark.asyncio
async def test_snapshot_indexed_by_ticket_and_run(tmp_path: Path) -> None:
    store = QualitySnapshotStore(tmp_path / "q.jsonl")
    await store.load()
    for tid in ("t1", "t1", "t2"):
        await store.append(
            QualitySnapshot(
                ticket_id=tid,
                run_id=f"r-{tid}",
                max_cc=0,
                ruff_findings=0,
                loc_delta=0,
                taxonomy_hit_counts={},
                cell={},
                spawned_reviewers=(),
                role_versions={},
            )
        )
    assert len(await store.for_ticket("t1")) == 2
    assert len(await store.for_ticket("t2")) == 1
    assert len(await store.for_run("r-t1")) == 2
    assert len(await store.all()) == 3
