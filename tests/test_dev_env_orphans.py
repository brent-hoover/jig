"""Tests for orphan tracking + jig dev CLI (Track E MVP)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from jig.cli import cli
from jig.dev_env.manifest import derive_manifest
from jig.dev_env.orphans import (
    OrphanLogEntry,
    OrphanTracker,
    append_orphan_log,
    drop_orphan,
    list_orphans,
    orphan_log_path,
)
from jig.dev_env.provisioning import (
    PostgresSchemaProvisioner,
    ProvisioningRegistry,
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
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _intent(p: str = "P", s: str = "S") -> Intent:
    return Intent(problem=p, simplest_solution=s)


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


# ---------------------------------------------------------------------------
# Detection semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_orphans_skips_live_tickets(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    from jig.spec_loader import load_dev_manifest

    manifest = load_dev_manifest(tmp_path)
    open_t = Ticket(
        id="t-open",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
        status=TicketStatus.OPEN,
        description=TICKET_AC_PLACEHOLDER,
    )
    inprog = Ticket(
        id="t-inprog",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
        status=TicketStatus.IN_PROGRESS,
        description=TICKET_AC_PLACEHOLDER,
    )
    rows = await list_orphans(manifest, [open_t, inprog])
    assert rows == []


@pytest.mark.asyncio
async def test_list_orphans_flags_terminal_tickets(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    from jig.spec_loader import load_dev_manifest

    manifest = load_dev_manifest(tmp_path)
    resolved = Ticket(
        id="t-001",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
        status=TicketStatus.RESOLVED,
        description=TICKET_AC_PLACEHOLDER,
    )
    failed = Ticket(
        id="t-002",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
        status=TicketStatus.FAILED,
        description=TICKET_AC_PLACEHOLDER,
    )
    rows = await list_orphans(manifest, [resolved, failed])
    ids = sorted(o.id for o in rows)
    assert ids == ["main-db:t-001", "main-db:t-002"]
    by_id = {o.id: o for o in rows}
    assert by_id["main-db:t-001"].namespace == "agent_t_001"
    assert by_id["main-db:t-001"].ticket_status == "resolved"
    assert by_id["main-db:t-002"].ticket_status == "failed"


@pytest.mark.asyncio
async def test_list_orphans_skips_non_shared_namespaced_strategies(
    tmp_path: Path,
):
    """per_agent_ephemeral / operator_supplied don't produce orphan candidates yet."""
    arch = Architecture(
        data_stores=[
            DataStore(
                id="ephem",
                kind="sqlite",
                dev_provisioning=DevProvisioning(strategy="per_agent_ephemeral"),
            ),
        ],
    )
    manifest = derive_manifest(arch)
    resolved = Ticket(
        id="t-1",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
        status=TicketStatus.RESOLVED,
        description=TICKET_AC_PLACEHOLDER,
    )
    rows = await list_orphans(manifest, [resolved])
    assert rows == []


# ---------------------------------------------------------------------------
# drop_orphan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drop_orphan_invokes_postgres_drop(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    from jig.spec_loader import load_dev_manifest

    manifest = load_dev_manifest(tmp_path)
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    resolved = Ticket(
        id="t-001",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
        status=TicketStatus.RESOLVED,
        description=TICKET_AC_PLACEHOLDER,
    )
    rows = await list_orphans(manifest, [resolved])
    assert len(rows) == 1
    await drop_orphan(manifest, rows[0], registry=reg)
    assert rec.calls == ["DROP SCHEMA IF EXISTS agent_t_001 CASCADE"]


@pytest.mark.asyncio
async def test_drop_orphan_archive_path(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    from jig.spec_loader import load_dev_manifest

    manifest = load_dev_manifest(tmp_path)
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    failed = Ticket(
        id="t-001",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="u",
        status=TicketStatus.FAILED,
        description=TICKET_AC_PLACEHOLDER,
    )
    rows = await list_orphans(manifest, [failed])
    await drop_orphan(manifest, rows[0], success_reason="failure_archive", registry=reg)
    assert rec.calls == ["ALTER SCHEMA agent_t_001 RENAME TO archived_agent_t_001"]


# ---------------------------------------------------------------------------
# Orphan log audit trail
# ---------------------------------------------------------------------------


def test_append_orphan_log_writes_one_line_per_call(tmp_path: Path):
    append_orphan_log(tmp_path, OrphanLogEntry(action="detect", orphan_count=2))
    append_orphan_log(
        tmp_path, OrphanLogEntry(action="drop", orphan_id="main-db:t-001")
    )
    body = orphan_log_path(tmp_path).read_text().strip().splitlines()
    assert len(body) == 2
    parsed = [json.loads(line) for line in body]
    assert parsed[0]["action"] == "detect"
    assert parsed[0]["orphan_count"] == 2
    assert parsed[1]["action"] == "drop"
    assert parsed[1]["orphan_id"] == "main-db:t-001"


# ---------------------------------------------------------------------------
# OrphanTracker facade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tracker_list_writes_audit_log(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    await store.create(
        Ticket(
            id="t-001",
            work_type=WorkType.FEATURE,
            title="x",
            created_by="u",
            status=TicketStatus.RESOLVED,
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    tracker = OrphanTracker(tmp_path, load_dev_manifest(tmp_path), store)
    rows = await tracker.list_orphans()
    assert len(rows) == 1
    body = orphan_log_path(tmp_path).read_text().strip().splitlines()
    assert len(body) == 1
    assert json.loads(body[0])["action"] == "detect"


@pytest.mark.asyncio
async def test_tracker_drop_returns_false_for_unknown_id(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    tracker = OrphanTracker(tmp_path, load_dev_manifest(tmp_path), store)
    assert await tracker.drop("nope:nada") is False


@pytest.mark.asyncio
async def test_tracker_drop_invokes_provisioner(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    await store.create(
        Ticket(
            id="t-001",
            work_type=WorkType.FEATURE,
            title="x",
            created_by="u",
            status=TicketStatus.RESOLVED,
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    tracker = OrphanTracker(tmp_path, load_dev_manifest(tmp_path), store, registry=reg)
    ok = await tracker.drop("main-db:t-001")
    assert ok is True
    assert rec.calls == ["DROP SCHEMA IF EXISTS agent_t_001 CASCADE"]


@pytest.mark.asyncio
async def test_tracker_purge_drops_all(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    from jig.spec_loader import load_dev_manifest

    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    for tid in ("t-001", "t-002"):
        await store.create(
            Ticket(
                id=tid,
                work_type=WorkType.FEATURE,
                title="x",
                created_by="u",
                status=TicketStatus.RESOLVED,
                description=TICKET_AC_PLACEHOLDER,
            )
        )
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    tracker = OrphanTracker(tmp_path, load_dev_manifest(tmp_path), store, registry=reg)
    n = await tracker.purge()
    assert n == 2
    assert any("agent_t_001" in c for c in rec.calls)
    assert any("agent_t_002" in c for c in rec.calls)


# ---------------------------------------------------------------------------
# CLI: jig dev manifest
# ---------------------------------------------------------------------------


def test_cli_dev_manifest_prints_yaml(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["dev", "manifest", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "main-db" in result.output
    assert "shared_namespaced" in result.output


def test_cli_dev_manifest_no_derive_reads_disk(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["dev", "manifest", "--path", str(tmp_path), "--no-derive"]
    )
    assert result.exit_code == 0
    assert "main-db" in result.output


def test_cli_dev_manifest_missing_architecture_errors(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["dev", "manifest", "--path", str(tmp_path)])
    assert result.exit_code != 0
    assert "architecture" in result.output


# ---------------------------------------------------------------------------
# CLI: jig dev orphans list / drop / purge
# ---------------------------------------------------------------------------


def _seed_resolved_ticket(tmp_path: Path, tid: str = "t-001") -> None:
    """Synchronous seed via the JSONL writer the TicketStore loads on startup."""
    import asyncio

    async def _seed() -> None:
        store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
        await store.load()
        await store.create(
            Ticket(
                id=tid,
                work_type=WorkType.FEATURE,
                title="x",
                created_by="u",
                status=TicketStatus.RESOLVED,
                description=TICKET_AC_PLACEHOLDER,
            )
        )

    asyncio.run(_seed())


def test_cli_dev_orphans_list_empty(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    (tmp_path / ".jig" / "store").mkdir(parents=True, exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(cli, ["dev", "orphans", "list", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "(no orphans)" in result.output


def test_cli_dev_orphans_list_shows_resolved(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    _seed_resolved_ticket(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["dev", "orphans", "list", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert "main-db:t-001" in result.output
    assert "namespace=agent_t_001" in result.output


def test_cli_dev_orphans_drop_unknown_errors(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    (tmp_path / ".jig" / "store").mkdir(parents=True, exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["dev", "orphans", "drop", "main-db:nope", "--path", str(tmp_path)],
    )
    assert result.exit_code != 0
    assert "not found" in result.output


def test_cli_dev_orphans_drop_known(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    _seed_resolved_ticket(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["dev", "orphans", "drop", "main-db:t-001", "--path", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "dropped main-db:t-001" in result.output


def test_cli_dev_orphans_purge_requires_confirm(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    (tmp_path / ".jig" / "store").mkdir(parents=True, exist_ok=True)
    runner = CliRunner()
    result = runner.invoke(cli, ["dev", "orphans", "purge", "--path", str(tmp_path)])
    assert result.exit_code != 0
    assert "--confirm" in result.output


def test_cli_dev_orphans_purge_with_confirm(tmp_path: Path):
    _seed_arch_and_manifest(tmp_path)
    _seed_resolved_ticket(tmp_path, tid="t-001")
    _seed_resolved_ticket(tmp_path, tid="t-002")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "dev",
            "orphans",
            "purge",
            "--confirm",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "purged 2 orphan(s)" in result.output


# Touch unused imports to make the linter happy when fixtures change
_ = (PostgresSchemaProvisioner,)
