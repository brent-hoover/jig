"""``jig audit quality`` — per-snapshot table + ``--by`` cell-key grouping."""

from __future__ import annotations

import asyncio
from pathlib import Path

from click.testing import CliRunner

from jig.cli import cli
from jig.store.quality import QualitySnapshot, QualitySnapshotStore


def _seed(tmp_path: Path, snaps: list[QualitySnapshot]) -> None:
    p = tmp_path / ".jig" / "store" / "quality_snapshots.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)

    async def _run() -> None:
        store = QualitySnapshotStore(p)
        await store.load()
        for s in snaps:
            await store.append(s)

    asyncio.run(_run())


def _snap(**overrides) -> QualitySnapshot:
    base = dict(
        ticket_id="t1",
        run_id="r1",
        max_cc=0,
        ruff_findings=0,
        loc_delta=0,
        taxonomy_hit_counts={},
        cell={},
        spawned_reviewers=(),
        role_versions={},
    )
    base.update(overrides)
    return QualitySnapshot(**base)


def test_audit_quality_empty(tmp_path: Path) -> None:
    res = CliRunner().invoke(cli, ["audit", "quality", "--path", str(tmp_path)])
    assert res.exit_code == 0
    assert "no quality snapshots" in res.output.lower()


def test_audit_quality_table(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        [
            _snap(ticket_id="t1", run_id="r1", max_cc=5, ruff_findings=0, loc_delta=10),
            _snap(
                ticket_id="t2",
                run_id="r2",
                max_cc=14,
                ruff_findings=3,
                loc_delta=120,
                taxonomy_hit_counts={"security": 1},
            ),
        ],
    )
    res = CliRunner().invoke(cli, ["audit", "quality", "--path", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "t1" in res.output and "t2" in res.output
    # Flagged CC visible at a glance.
    assert "14" in res.output
    # Taxonomy categories rendered inline.
    assert "security:1" in res.output


def test_audit_quality_filter_by_ticket(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        [_snap(ticket_id="t1", run_id="r1"), _snap(ticket_id="t2", run_id="r2")],
    )
    res = CliRunner().invoke(
        cli, ["audit", "quality", "--ticket-id", "t1", "--path", str(tmp_path)]
    )
    assert res.exit_code == 0
    assert "t1" in res.output
    assert "t2" not in res.output


def test_audit_quality_filter_by_run(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        [_snap(ticket_id="t1", run_id="r1"), _snap(ticket_id="t2", run_id="r2")],
    )
    res = CliRunner().invoke(
        cli, ["audit", "quality", "--run-id", "r2", "--path", str(tmp_path)]
    )
    assert res.exit_code == 0
    assert "t2" in res.output
    assert "t1" not in res.output


def test_audit_quality_group_by_cell_key(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        [
            _snap(ticket_id="t1", run_id="r1", max_cc=4, cell={"layer": "mvp"}),
            _snap(
                ticket_id="t2",
                run_id="r2",
                max_cc=12,
                cell={"layer": "mvp"},
                taxonomy_hit_counts={"security": 2},
            ),
            _snap(ticket_id="t3", run_id="r3", max_cc=2, cell={"layer": "final"}),
        ],
    )
    res = CliRunner().invoke(
        cli, ["audit", "quality", "--by", "layer", "--path", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output
    # Two groups visible.
    assert "mvp" in res.output
    assert "final" in res.output
    # Header row hints at aggregate columns.
    assert "cc_avg" in res.output or "cc avg" in res.output.lower()
    # Aggregated taxonomy total rendered.
    assert "security:2" in res.output


def test_audit_quality_ticket_and_run_mutually_exclusive(tmp_path: Path) -> None:
    """Passing both ``--ticket-id`` and ``--run-id`` is ambiguous — the CLI
    must surface a UsageError rather than silently honour one and drop the
    other."""
    _seed(tmp_path, [_snap(ticket_id="t1", run_id="r1")])
    res = CliRunner().invoke(
        cli,
        [
            "audit",
            "quality",
            "--ticket-id",
            "t1",
            "--run-id",
            "r1",
            "--path",
            str(tmp_path),
        ],
    )
    assert res.exit_code != 0
    assert "mutually exclusive" in res.output.lower()


def test_audit_quality_days_filter(tmp_path: Path) -> None:
    """``--days N`` keeps snapshots recorded within the last N days and
    drops older rows."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    fresh = _snap(ticket_id="fresh", run_id="r-fresh")
    stale = _snap(ticket_id="stale", run_id="r-stale")
    stale = stale.model_copy(update={"recorded_at": now - timedelta(days=30)})
    _seed(tmp_path, [fresh, stale])

    res = CliRunner().invoke(
        cli, ["audit", "quality", "--days", "7", "--path", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output
    assert "fresh" in res.output
    assert "stale" not in res.output
