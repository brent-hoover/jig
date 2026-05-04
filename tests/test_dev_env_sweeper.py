"""Tests for the orphan sweeper with operator-confirmation (Track E Final)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

from jig.cli import cli
from jig.dev_env.manifest import derive_manifest
from jig.dev_env.provisioning import ProvisioningRegistry
from jig.dev_env.sweeper import (
    OrphanSweeper,
    SweepBucket,
    SweepReport,
    sweeper_log_path,
)
from jig.intent import Intent
from jig.schemas.arch import (
    Architecture,
    DataStore,
    DevProvisioning,
    Module,
)
from jig.spec_loader import save_architecture, save_dev_manifest
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType


def _intent() -> Intent:
    return Intent(problem="P", simplest_solution="S")


def _seed_arch_and_manifest(tmp_path: Path) -> None:
    arch = Architecture(
        data_stores=[
            DataStore(
                id="main-db",
                kind="postgres",
                dev_provisioning=DevProvisioning(
                    strategy="shared_namespaced",
                    namespace_template="agent_{ticket_id}",
                ),
            ),
        ],
        modules=[
            Module(
                id="catalog-ingest",
                title="t",
                summary="s",
                intent=_intent(),
            )
        ],
    )
    save_architecture(tmp_path, arch)
    save_dev_manifest(tmp_path, derive_manifest(arch))


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, sql: str) -> None:
        self.calls.append(sql)


async def _seed_ticket(
    tmp_path: Path,
    *,
    tid: str,
    status: TicketStatus,
    age_days: int = 0,
) -> None:
    """Create a terminal-status ticket with optional `updated_at` backdating."""
    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    ticket = Ticket(
        id=tid,
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
        status=status,
    )
    if age_days:
        old = datetime.now(timezone.utc) - timedelta(days=age_days)
        ticket.updated_at = old
    await store.create(ticket)


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_categorizes_old_resolved_as_auto_safe(tmp_path: Path) -> None:
    """Older than threshold + status=RESOLVED → auto_safe bucket."""
    _seed_arch_and_manifest(tmp_path)
    await _seed_ticket(
        tmp_path, tid="t-old", status=TicketStatus.RESOLVED, age_days=14
    )
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    sweeper = OrphanSweeper(
        tmp_path, load_dev_manifest(tmp_path), store, threshold_days=7
    )
    report = await sweeper.run()
    assert len(report.auto_safe) == 1
    assert report.auto_safe[0].ticket_id == "t-old"
    assert report.needs_confirm == []
    assert report.keep == []


@pytest.mark.asyncio
async def test_sweep_categorizes_recent_failed_as_needs_confirm(
    tmp_path: Path,
) -> None:
    """Recent FAILED → needs_confirm (operator should look)."""
    _seed_arch_and_manifest(tmp_path)
    await _seed_ticket(
        tmp_path, tid="t-failed", status=TicketStatus.FAILED, age_days=1
    )
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    sweeper = OrphanSweeper(
        tmp_path, load_dev_manifest(tmp_path), store, threshold_days=7
    )
    report = await sweeper.run()
    assert report.auto_safe == []
    assert len(report.needs_confirm) == 1
    assert report.needs_confirm[0].ticket_id == "t-failed"


@pytest.mark.asyncio
async def test_sweep_keeps_very_recent_resolved(tmp_path: Path) -> None:
    """Recent RESOLVED (under threshold) → keep bucket."""
    _seed_arch_and_manifest(tmp_path)
    await _seed_ticket(
        tmp_path, tid="t-fresh", status=TicketStatus.RESOLVED, age_days=0
    )
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    sweeper = OrphanSweeper(
        tmp_path, load_dev_manifest(tmp_path), store, threshold_days=7
    )
    report = await sweeper.run()
    assert report.auto_safe == []
    assert report.needs_confirm == []
    assert len(report.keep) == 1
    assert report.keep[0].ticket_id == "t-fresh"


@pytest.mark.asyncio
async def test_sweep_skips_live_tickets(tmp_path: Path) -> None:
    _seed_arch_and_manifest(tmp_path)
    await _seed_ticket(
        tmp_path, tid="t-open", status=TicketStatus.OPEN, age_days=30
    )
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    sweeper = OrphanSweeper(
        tmp_path, load_dev_manifest(tmp_path), store, threshold_days=7
    )
    report = await sweeper.run()
    assert report.auto_safe == []
    assert report.needs_confirm == []
    assert report.keep == []


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sweep_apply_auto_safe_drops_orphans(tmp_path: Path) -> None:
    _seed_arch_and_manifest(tmp_path)
    await _seed_ticket(
        tmp_path, tid="t-old", status=TicketStatus.RESOLVED, age_days=14
    )
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    sweeper = OrphanSweeper(
        tmp_path,
        load_dev_manifest(tmp_path),
        store,
        threshold_days=7,
        registry=reg,
    )
    report = await sweeper.run()
    n = await sweeper.apply(report, bucket=SweepBucket.AUTO_SAFE)
    assert n == 1
    assert any("agent_t_old" in c for c in rec.calls)


@pytest.mark.asyncio
async def test_sweep_apply_needs_confirm_drops_orphans(tmp_path: Path) -> None:
    _seed_arch_and_manifest(tmp_path)
    await _seed_ticket(
        tmp_path, tid="t-failed", status=TicketStatus.FAILED, age_days=1
    )
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    sweeper = OrphanSweeper(
        tmp_path,
        load_dev_manifest(tmp_path),
        store,
        threshold_days=7,
        registry=reg,
    )
    report = await sweeper.run()
    n = await sweeper.apply(report, bucket=SweepBucket.NEEDS_CONFIRM)
    assert n == 1


@pytest.mark.asyncio
async def test_sweep_apply_all_drops_both_buckets(tmp_path: Path) -> None:
    _seed_arch_and_manifest(tmp_path)
    await _seed_ticket(
        tmp_path, tid="t-old", status=TicketStatus.RESOLVED, age_days=14
    )
    await _seed_ticket(
        tmp_path, tid="t-failed", status=TicketStatus.FAILED, age_days=1
    )
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    sweeper = OrphanSweeper(
        tmp_path,
        load_dev_manifest(tmp_path),
        store,
        threshold_days=7,
        registry=reg,
    )
    report = await sweeper.run()
    n = await sweeper.apply(report, bucket=SweepBucket.ALL)
    assert n == 2


@pytest.mark.asyncio
async def test_sweep_apply_writes_audit_log(tmp_path: Path) -> None:
    _seed_arch_and_manifest(tmp_path)
    await _seed_ticket(
        tmp_path, tid="t-old", status=TicketStatus.RESOLVED, age_days=14
    )
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    sweeper = OrphanSweeper(
        tmp_path,
        load_dev_manifest(tmp_path),
        store,
        threshold_days=7,
        registry=reg,
    )
    report = await sweeper.run()
    await sweeper.apply(report, bucket=SweepBucket.AUTO_SAFE)
    log = sweeper_log_path(tmp_path)
    assert log.is_file()
    rows = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    # One row for the run + one for the apply
    actions = [r["action"] for r in rows]
    assert "run" in actions
    assert "apply" in actions


# ---------------------------------------------------------------------------
# CLI: jig dev sweeper
# ---------------------------------------------------------------------------


def test_cli_sweeper_run_empty_report(tmp_path: Path) -> None:
    _seed_arch_and_manifest(tmp_path)
    (tmp_path / ".jig" / "store").mkdir(parents=True, exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "sweeper", "run", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "auto_safe" in result.output
    assert "needs_confirm" in result.output


def test_cli_sweeper_run_categorizes_tickets(tmp_path: Path) -> None:
    import asyncio

    _seed_arch_and_manifest(tmp_path)
    asyncio.run(
        _seed_ticket(
            tmp_path, tid="t-old", status=TicketStatus.RESOLVED, age_days=14
        )
    )
    asyncio.run(
        _seed_ticket(
            tmp_path, tid="t-failed", status=TicketStatus.FAILED, age_days=1
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "sweeper", "run", "--path", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "t-old" in result.output
    assert "t-failed" in result.output


def test_cli_sweeper_apply_auto_safe_no_confirm_required(tmp_path: Path) -> None:
    """auto_safe bucket doesn't need --confirm."""
    import asyncio

    _seed_arch_and_manifest(tmp_path)
    asyncio.run(
        _seed_ticket(
            tmp_path, tid="t-old", status=TicketStatus.RESOLVED, age_days=14
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dev",
            "sweeper",
            "apply",
            "--bucket",
            "auto_safe",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "applied" in result.output.lower() or "dropped" in result.output.lower()


def test_cli_sweeper_apply_needs_confirm_requires_flag(tmp_path: Path) -> None:
    import asyncio

    _seed_arch_and_manifest(tmp_path)
    asyncio.run(
        _seed_ticket(
            tmp_path, tid="t-failed", status=TicketStatus.FAILED, age_days=1
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dev",
            "sweeper",
            "apply",
            "--bucket",
            "needs_confirm",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "--confirm" in result.output


def test_cli_sweeper_apply_needs_confirm_with_flag_drops(tmp_path: Path) -> None:
    import asyncio

    _seed_arch_and_manifest(tmp_path)
    asyncio.run(
        _seed_ticket(
            tmp_path, tid="t-failed", status=TicketStatus.FAILED, age_days=1
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dev",
            "sweeper",
            "apply",
            "--bucket",
            "needs_confirm",
            "--confirm",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output


def test_cli_sweeper_apply_all_requires_confirm(tmp_path: Path) -> None:
    import asyncio

    _seed_arch_and_manifest(tmp_path)
    asyncio.run(
        _seed_ticket(
            tmp_path, tid="t-old", status=TicketStatus.RESOLVED, age_days=14
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dev",
            "sweeper",
            "apply",
            "--bucket",
            "all",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "--confirm" in result.output


# ---------------------------------------------------------------------------
# Direct construction / typing sanity
# ---------------------------------------------------------------------------


def test_sweep_report_serializes_buckets() -> None:
    r = SweepReport()
    j = r.model_dump_json()
    assert "auto_safe" in j
    assert "needs_confirm" in j
    assert "keep" in j
