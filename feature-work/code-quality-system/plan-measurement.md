---
title: Sub-issue D — Quality Measurement + Attribution — Implementation Plan
type: plan
status: active
owner: Brent Hoover
created: 2026-06-01
updated: 2026-06-02
design: ./design.md
---

# Quality Measurement + Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Record a per-end-of-ticket `QualitySnapshot` (the metrics A/B/C now produce — max CC, ruff finding
count, LoC delta, taxonomy hit counts by category) tagged with a small "cell" of attribution keys (ticket
layer / workflow / spawned reviewers), and surface it through a `jig audit quality` CLI so the operator can
read quality over time and across cells.

**Architecture:** The existing `AuditStore` records per-fix events (rule applications) — wrong shape for a
per-run quality summary. D adds a sibling **`QualitySnapshotStore`** at
`.jig/store/quality_snapshots.jsonl`. The recording hook sits in `dispatch_with_llm_spawn` immediately after
`compute_change_metrics` returns (already computed once per end-of-ticket dispatch, with `taxonomy_hits`
attached). The CLI lives under the existing `audit` group as `jig audit quality`, with filters and a
`--by <key>` grouping that aggregates across snapshots — the substrate for the "what moved the metric"
analysis the design's north star promises. A full `--compare cellA cellB` delta view is deferred to a
follow-up once we have real snapshots to validate the aggregation against.

**Resolves design open questions:**
- `quality_snapshot` schema: **new pydantic model + sibling JSONL store** (not extended `AuditEntry`). The
  shapes are different — one is per-fix, the other per-run summary — so separation keeps queries simple.
- Eval-coverage scoreboard: **deferred** to a follow-up sub-issue. D ships the recording substrate; the
  scoreboard (how often agents emit pattern X vs. how often the federation catches it) needs a corpus and
  a separate UI and isn't load-bearing for the measurement story.

**Tech Stack:** Python 3.12, pydantic v2, click, pytest. No new deps.

## Scope (sub-issue D = #112)

In: `QualitySnapshot` model + store, recording hook in dispatch, `jig audit quality` CLI with filters and
grouping, tests. Out: a full `--compare` cell-vs-cell delta view (follow-up), eval-coverage scoreboard
(deferred), graphical visualization (CLI report only).

## Resolved scope decisions

- **Cell-tag fields.** `workflow_name`, `layer`, `work_type`, `spawned_reviewers` (sorted tuple of LLM
  reviewer ids that ran), plus a `role_versions` map `{reviewer_id: short_sha}` where `short_sha` is the
  first 12 chars of `sha256_hex(role_yaml_bytes)` for the reviewer's role config (read from
  `.jig/roles/<id>.yaml` at snapshot time). Survives role-prompt edits — the literal "what changed"
  attribution the design promised. Missing role files contribute `""` for that key (don't crash).

## File structure

- `jig/store/quality.py` (new) — `QualitySnapshot` model + `QualitySnapshotStore` (append-only JSONL).
- `jig/reviewers/dispatch.py` (modify) — after `compute_change_metrics`, also persist a snapshot.
- `jig/cli.py` (modify) — add `audit_group.command("quality")`.
- `tests/test_quality_snapshot_store.py` (new) — model + store round-trip.
- `tests/test_quality_snapshot_recording.py` (new) — dispatch records a snapshot per end-of-ticket run.
- `tests/test_cli_audit_quality.py` (new) — CLI output, filters, grouping.

## Task 1: `QualitySnapshot` model + `QualitySnapshotStore` (TDD)

**Files:**
- Create: `jig/store/quality.py`
- Test: `tests/test_quality_snapshot_store.py`

- [ ] **Step 1: Write the failing test**

```python
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
    )
    await store.append(snap)
    again = QualitySnapshotStore(tmp_path / "q.jsonl")
    await again.load()
    rows = await again.for_ticket("t1")
    assert len(rows) == 1
    r = rows[0]
    assert r.max_cc == 12
    assert r.taxonomy_hit_counts["error-handling"] == 2
    assert r.cell["workflow_name"] == "default"
    assert r.spawned_reviewers == ("reviewer-security",)


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
            )
        )
    assert len(await store.for_ticket("t1")) == 2
    assert len(await store.for_ticket("t2")) == 1
    assert len(await store.all()) == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_quality_snapshot_store.py -q`
Expected: FAIL — `jig.store.quality` doesn't exist.

- [ ] **Step 3: Implement the model + store**

`jig/store/quality.py`:

```python
"""QualitySnapshotStore — append-only JSONL of per-end-of-ticket quality
snapshots (radon CC, ruff findings, LoC delta, taxonomy hit counts) tagged
with attribution cells.

Separate from ``AuditStore`` because the shape is different: AuditEntry is
per-rule-application; QualitySnapshot is a per-run summary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import ConfigDict, Field

from jig.store.collection import Collection
from jig.store.models import StoreModel


class QualitySnapshot(StoreModel):
    """One per-end-of-ticket quality summary."""

    model_config = ConfigDict(frozen=True)

    ticket_id: str
    run_id: str
    max_cc: int = Field(ge=0)
    ruff_findings: int = Field(ge=0)
    loc_delta: int
    taxonomy_hit_counts: dict[str, int] = Field(default_factory=dict)
    cell: dict[str, str] = Field(default_factory=dict)
    spawned_reviewers: tuple[str, ...] = ()
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QualitySnapshotStore:
    """Append-only JSONL store for ``QualitySnapshot`` records."""

    def __init__(self, path: Path) -> None:
        self._collection = Collection(path, index_fields=["ticket_id", "run_id"])

    async def load(self) -> None:
        await self._collection.load()

    async def append(self, snap: QualitySnapshot) -> str:
        return await self._collection.insert(snap.model_dump(mode="json"))

    def _load(self, raw: dict) -> QualitySnapshot:
        return QualitySnapshot.model_validate(raw)

    async def for_ticket(self, ticket_id: str) -> list[QualitySnapshot]:
        return [self._load(r) for r in await self._collection.find_where(ticket_id=ticket_id)]

    async def for_run(self, run_id: str) -> list[QualitySnapshot]:
        return [self._load(r) for r in await self._collection.find_where(run_id=run_id)]

    async def all(self) -> list[QualitySnapshot]:
        return [self._load(r) for r in await self._collection.find()]


__all__ = ["QualitySnapshot", "QualitySnapshotStore"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_quality_snapshot_store.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/store/quality.py tests/test_quality_snapshot_store.py
git commit -m "feat(code-quality): QualitySnapshot model + store (#112)"
```

## Task 2: record a snapshot per end-of-ticket dispatch (TDD)

**Files:**
- Modify: `jig/reviewers/dispatch.py` (after `compute_change_metrics`, also persist a snapshot)
- Test: `tests/test_quality_snapshot_recording.py`

- [ ] **Step 1: Write the failing test**

```python
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

    store = QualitySnapshotStore(tmp_path / ".jig" / "store" / "quality_snapshots.jsonl")
    await store.load()
    snaps = await store.for_ticket("tb-fed")
    assert len(snaps) == 1, snaps
    s = snaps[0]
    assert s.max_cc == 12
    assert s.ruff_findings == 2
    assert s.loc_delta == 42
    assert s.taxonomy_hit_counts == {"security": 1, "error-handling": 1}
    # Cell carries enough for attribution.
    assert s.cell.get("workflow_name")
    assert s.cell.get("layer") == "mvp"
    # spawned_reviewers reflects what dispatch queued (sorted for stability).
    assert "reviewer-security" in s.spawned_reviewers
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_quality_snapshot_recording.py -q`
Expected: FAIL — no snapshot is written; the store file doesn't exist.

- [ ] **Step 3: Add the recording hook**

In `jig/reviewers/dispatch.py`, immediately after the unrouted-hit warning loop (so it runs on every
dispatch path, including `reviewers=[]`), persist a snapshot:

```python
if code_metrics is not None:
    from collections import Counter

    from jig.store.quality import QualitySnapshot, QualitySnapshotStore

    counts = Counter(h.category for h in code_metrics.taxonomy_hits)
    snap = QualitySnapshot(
        ticket_id=ticket.id,
        run_id=f"{ticket.id}.cycle{cycle}",
        max_cc=code_metrics.max_cc,
        ruff_findings=code_metrics.ruff_findings,
        loc_delta=code_metrics.loc_delta,
        taxonomy_hit_counts=dict(counts),
        cell={
            "workflow_name": getattr(ticket, "workflow", "") or "",
            "layer": getattr(ticket, "layer", "") or "",
            "work_type": getattr(ticket.work_type, "value", str(ticket.work_type)),
        },
        spawned_reviewers=tuple(sorted(spawned_reviewer_ids)),
    )
    snap_path = project_root / ".jig" / "store" / "quality_snapshots.jsonl"
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_store = QualitySnapshotStore(snap_path)
    await snap_store.load()
    try:
        await snap_store.append(snap)
    except Exception:  # noqa: BLE001 — signal-only contract
        _logger.warning(
            "quality snapshot append failed for ticket %s; continuing",
            ticket.id,
            exc_info=True,
        )
```

Place this right after the existing `for hit in code_metrics.taxonomy_hits:` warning loop and BEFORE the
`if not pendings: return out` early-return — so a `reviewers=[]` dispatch still records.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_quality_snapshot_recording.py tests/test_reviewers_federation_execution.py -q`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add jig/reviewers/dispatch.py tests/test_quality_snapshot_recording.py
git commit -m "feat(code-quality): record QualitySnapshot per end-of-ticket dispatch (#112)"
```

## Task 3: `jig audit quality` CLI (TDD)

**Files:**
- Modify: `jig/cli.py` (add `audit_group.command("quality")`)
- Test: `tests/test_cli_audit_quality.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
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
    )
    base.update(overrides)
    return QualitySnapshot(**base)


def test_audit_quality_empty(tmp_path: Path) -> None:
    res = CliRunner().invoke(cli, ["audit", "quality", "--path", str(tmp_path)])
    assert res.exit_code == 0
    assert "no quality snapshots" in res.output.lower()


def test_audit_quality_table(tmp_path: Path) -> None:
    _seed(tmp_path, [
        _snap(ticket_id="t1", run_id="r1", max_cc=5, ruff_findings=0, loc_delta=10),
        _snap(ticket_id="t2", run_id="r2", max_cc=14, ruff_findings=3, loc_delta=120,
              taxonomy_hit_counts={"security": 1}),
    ])
    res = CliRunner().invoke(cli, ["audit", "quality", "--path", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "t1" in res.output and "t2" in res.output
    # Flagged-CC row should be visible at a glance.
    assert "14" in res.output
    assert "security:1" in res.output or "security: 1" in res.output


def test_audit_quality_filter_by_ticket(tmp_path: Path) -> None:
    _seed(tmp_path, [
        _snap(ticket_id="t1", run_id="r1"),
        _snap(ticket_id="t2", run_id="r2"),
    ])
    res = CliRunner().invoke(
        cli, ["audit", "quality", "--ticket-id", "t1", "--path", str(tmp_path)]
    )
    assert res.exit_code == 0
    assert "t1" in res.output
    assert "t2" not in res.output


def test_audit_quality_group_by_cell_key(tmp_path: Path) -> None:
    _seed(tmp_path, [
        _snap(ticket_id="t1", run_id="r1", max_cc=4, cell={"layer": "mvp"}),
        _snap(ticket_id="t2", run_id="r2", max_cc=12, cell={"layer": "mvp"}),
        _snap(ticket_id="t3", run_id="r3", max_cc=2, cell={"layer": "final"}),
    ])
    res = CliRunner().invoke(
        cli, ["audit", "quality", "--by", "layer", "--path", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output
    # Two rows: mvp (avg over 2 snapshots) and final (1 snapshot).
    assert "mvp" in res.output
    assert "final" in res.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli_audit_quality.py -q`
Expected: FAIL — no `audit quality` subcommand yet.

- [ ] **Step 3: Add the CLI command**

In `jig/cli.py`, after `audit_report` (which lives under `audit_group`), add:

```python
@audit_group.command("quality")
@click.option("--ticket-id", "ticket_id", default=None, help="Filter to a single ticket.")
@click.option("--run-id", "run_id", default=None, help="Filter to a single run.")
@click.option("--days", default=None, type=int, help="Last N days.")
@click.option(
    "--by",
    "group_by",
    default=None,
    type=str,
    help="Group by a cell key (e.g. ``layer``, ``workflow_name``).",
)
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def audit_quality(
    ticket_id: str | None,
    run_id: str | None,
    days: int | None,
    group_by: str | None,
    path: Path,
) -> None:
    """Print per-end-of-ticket quality snapshots (CC, ruff findings, LoC delta,
    taxonomy hit counts) with optional ``--by <cell-key>`` aggregation."""
    from datetime import datetime, timedelta, timezone
    from statistics import mean

    from jig.store.quality import QualitySnapshotStore

    snap_path = path / ".jig" / "store" / "quality_snapshots.jsonl"
    if not snap_path.is_file():
        click.echo("no quality snapshots found (.jig/store/quality_snapshots.jsonl missing)")
        return

    async def _load() -> list:
        store = QualitySnapshotStore(snap_path)
        await store.load()
        if ticket_id is not None:
            return await store.for_ticket(ticket_id)
        if run_id is not None:
            return await store.for_run(run_id)
        return await store.all()

    snaps = asyncio.run(_load())
    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        snaps = [s for s in snaps if s.recorded_at >= cutoff]
    if not snaps:
        click.echo("no quality snapshots match the given filters")
        return

    if group_by:
        # Aggregate: average max_cc / ruff_findings / loc_delta + total
        # taxonomy hits, grouped by the given cell key.
        buckets: dict[str, list] = {}
        for s in snaps:
            key = s.cell.get(group_by, "(none)")
            buckets.setdefault(key, []).append(s)
        click.echo(f"{group_by:<20}  {'n':>3}  {'cc_avg':>6}  {'ruff_avg':>8}  {'loc_avg':>7}  taxonomy_total")
        click.echo(f"{'-' * 20}  {'-' * 3}  {'-' * 6}  {'-' * 8}  {'-' * 7}  --------------")
        for key, group in sorted(buckets.items()):
            cc = mean(s.max_cc for s in group)
            ruff = mean(s.ruff_findings for s in group)
            loc = mean(s.loc_delta for s in group)
            tax_total: dict[str, int] = {}
            for s in group:
                for cat, n in s.taxonomy_hit_counts.items():
                    tax_total[cat] = tax_total.get(cat, 0) + n
            tax_render = " ".join(f"{k}:{v}" for k, v in sorted(tax_total.items())) or "-"
            click.echo(
                f"{key:<20}  {len(group):>3}  {cc:>6.1f}  {ruff:>8.1f}  {loc:>+7.1f}  {tax_render}"
            )
        return

    # Per-snapshot table.
    click.echo(f"{'ticket':<12}  {'cc':>3}  {'ruff':>4}  {'loc':>5}  taxonomy  recorded_at")
    click.echo(f"{'-' * 12}  {'-' * 3}  {'-' * 4}  {'-' * 5}  --------  -----------")
    for s in sorted(snaps, key=lambda s: s.recorded_at):
        tax_render = " ".join(f"{k}:{v}" for k, v in sorted(s.taxonomy_hit_counts.items())) or "-"
        click.echo(
            f"{s.ticket_id:<12}  {s.max_cc:>3}  {s.ruff_findings:>4}  {s.loc_delta:>+5}  "
            f"{tax_render}  {s.recorded_at.isoformat(timespec='seconds')}"
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli_audit_quality.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/cli.py tests/test_cli_audit_quality.py
git commit -m "feat(code-quality): jig audit quality CLI for snapshots + grouping (#112)"
```

## Task 4: full gate

- [ ] `uv run ruff check jig/ tests/` clean.
- [ ] `uv run ruff format --check` clean on changed files.
- [ ] `uv run pytest tests/ -q` — full suite green; expect baseline + ~10 new tests.

## Rollback

Additive: new module + new store file + new CLI subcommand. Revert the branch. No migration — the existing
`.jig/store/audit.jsonl` is untouched.

## Out of scope for this plan

- A full `--compare cellA cellB` delta view (follow-up once snapshots accumulate; the `--by` grouping is the
  substrate it would build on).
- Eval-coverage scoreboard (separate follow-up — needs a corpus + its own UI).
- Capturing role/workflow YAML hashes for tighter cell tags (called out in the open scope decision; can be
  added during execution if you say yes).
- Graphical visualisation (CLI report only).

## Change log

- 2026-06-01: Initial draft (Brent Hoover)
