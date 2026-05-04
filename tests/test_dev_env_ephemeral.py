"""Tests for per_agent_ephemeral provisioners (Track E Final).

Covers ``SqliteEphemeralProvisioner`` + ``PostgresDbEphemeralProvisioner``
plus the registry dispatch path that maps ``kind`` × strategy to the
right provisioner. The shared cleanup-policy contract (drop / archive /
keep) is exercised per-provisioner so the operator's choice in
architecture YAML lands the way the design promises.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.dev_env.ephemeral import (
    EphemeralProvisioner,
    PostgresDbEphemeralProvisioner,
    SqliteEphemeralProvisioner,
    ephemeral_archive_dir,
    ephemeral_root,
)
from jig.dev_env.provisioning import (
    ProvisioningRegistry,
    cleanup_agent_namespace,
    provision_agent_namespace,
)
from jig.schemas.dev_env import DevManifest, ManifestService


# ---------------------------------------------------------------------------
# In-memory SQL recorder (mirrors test_dev_env_provisioning's pattern)
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, sql: str) -> None:
        self.calls.append(sql)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_ephemeral_root_lives_under_jig_dev(tmp_path: Path) -> None:
    """``.jig/dev/ephemeral/`` is the root for SQLite per-agent files."""
    root = ephemeral_root(tmp_path)
    assert root == tmp_path / ".jig" / "dev" / "ephemeral"


def test_ephemeral_archive_dir_lives_under_jig_dev(tmp_path: Path) -> None:
    """``.jig/dev/archive/`` is the destination for archived SQLite files."""
    arc = ephemeral_archive_dir(tmp_path)
    assert arc == tmp_path / ".jig" / "dev" / "archive"


# ---------------------------------------------------------------------------
# SqliteEphemeralProvisioner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sqlite_provision_creates_file_and_returns_url(
    tmp_path: Path,
) -> None:
    p = SqliteEphemeralProvisioner(project_root=tmp_path)
    svc = ManifestService(
        id="ephem",
        kind="sqlite",
        strategy="per_agent_ephemeral",
        namespace_template="agent_{ticket_id}",
    )
    url = await p.provision(svc, agent_id="dev", ticket_id="t-001")
    db_path = (
        tmp_path / ".jig" / "dev" / "ephemeral" / "ephem"
        / "agent_t_001.db"
    )
    assert db_path.is_file()
    assert url.startswith("sqlite:///")
    assert "agent_t_001.db" in url


@pytest.mark.asyncio
async def test_sqlite_cleanup_drop_removes_file(tmp_path: Path) -> None:
    p = SqliteEphemeralProvisioner(project_root=tmp_path)
    svc = ManifestService(
        id="ephem",
        kind="sqlite",
        strategy="per_agent_ephemeral",
        namespace_template="agent_{ticket_id}",
        cleanup_on_success="drop",
        cleanup_on_failure="archive",
    )
    await p.provision(svc, agent_id="dev", ticket_id="t-001")
    db_path = (
        tmp_path / ".jig" / "dev" / "ephemeral" / "ephem"
        / "agent_t_001.db"
    )
    assert db_path.is_file()
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=True)
    assert not db_path.is_file()


@pytest.mark.asyncio
async def test_sqlite_cleanup_archive_renames_file(tmp_path: Path) -> None:
    p = SqliteEphemeralProvisioner(project_root=tmp_path)
    svc = ManifestService(
        id="ephem",
        kind="sqlite",
        strategy="per_agent_ephemeral",
        namespace_template="agent_{ticket_id}",
        cleanup_on_success="drop",
        cleanup_on_failure="archive",
    )
    await p.provision(svc, agent_id="dev", ticket_id="t-001")
    db_path = (
        tmp_path / ".jig" / "dev" / "ephemeral" / "ephem"
        / "agent_t_001.db"
    )
    assert db_path.is_file()
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=False)
    assert not db_path.is_file()
    arc = ephemeral_archive_dir(tmp_path)
    archived = list(arc.glob("*_ephem_*agent_t_001*.db.bak"))
    assert len(archived) == 1


@pytest.mark.asyncio
async def test_sqlite_cleanup_keep_leaves_file(tmp_path: Path) -> None:
    p = SqliteEphemeralProvisioner(project_root=tmp_path)
    svc = ManifestService(
        id="ephem",
        kind="sqlite",
        strategy="per_agent_ephemeral",
        namespace_template="agent_{ticket_id}",
        cleanup_on_success="keep",
        cleanup_on_failure="keep",
    )
    await p.provision(svc, agent_id="dev", ticket_id="t-001")
    db_path = (
        tmp_path / ".jig" / "dev" / "ephemeral" / "ephem"
        / "agent_t_001.db"
    )
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=True)
    assert db_path.is_file()
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=False)
    assert db_path.is_file()


@pytest.mark.asyncio
async def test_sqlite_cleanup_missing_file_swallowed(tmp_path: Path) -> None:
    """Cleanup is best-effort; missing file must not raise."""
    p = SqliteEphemeralProvisioner(project_root=tmp_path)
    svc = ManifestService(
        id="ephem",
        kind="sqlite",
        strategy="per_agent_ephemeral",
        namespace_template="agent_{ticket_id}",
    )
    # No prior provision; cleanup must no-op rather than raising.
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=True)
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=False)


@pytest.mark.asyncio
async def test_sqlite_namespace_template_substitutes_agent_and_ticket(
    tmp_path: Path,
) -> None:
    p = SqliteEphemeralProvisioner(project_root=tmp_path)
    svc = ManifestService(
        id="ephem",
        kind="sqlite",
        strategy="per_agent_ephemeral",
        namespace_template="{agent_id}__{ticket_id}",
    )
    await p.provision(svc, agent_id="bones-agent", ticket_id="T-42-Foo")
    db_path = (
        tmp_path / ".jig" / "dev" / "ephemeral" / "ephem"
        / "bones_agent__t_42_foo.db"
    )
    assert db_path.is_file()


# ---------------------------------------------------------------------------
# PostgresDbEphemeralProvisioner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_db_ephemeral_provision_runs_create_database() -> None:
    rec = _Recorder()
    p = PostgresDbEphemeralProvisioner(sql_executor=rec)
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="per_agent_ephemeral",
        namespace_template="jig_eph_{agent_id}_{ticket_id}",
    )
    url = await p.provision(svc, agent_id="dev", ticket_id="t-001")
    assert rec.calls == ["CREATE DATABASE jig_eph_dev_t_001"]
    # The provisioner returns a URL placeholder that names the database.
    assert "jig_eph_dev_t_001" in url


@pytest.mark.asyncio
async def test_postgres_db_ephemeral_provision_no_executor_no_op() -> None:
    """Mirror the MVP shared_namespaced provisioner: no executor → log + return."""
    p = PostgresDbEphemeralProvisioner()
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="per_agent_ephemeral",
        namespace_template="jig_eph_{ticket_id}",
    )
    url = await p.provision(svc, agent_id="dev", ticket_id="t-001")
    # No executor → URL still produced (just no DB creation actually fired).
    assert "jig_eph_t_001" in url


@pytest.mark.asyncio
async def test_postgres_db_ephemeral_cleanup_drop() -> None:
    rec = _Recorder()
    p = PostgresDbEphemeralProvisioner(sql_executor=rec)
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="per_agent_ephemeral",
        namespace_template="jig_eph_{ticket_id}",
        cleanup_on_success="drop",
    )
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=True)
    assert rec.calls == ["DROP DATABASE IF EXISTS jig_eph_t_001"]


@pytest.mark.asyncio
async def test_postgres_db_ephemeral_cleanup_archive() -> None:
    rec = _Recorder()
    p = PostgresDbEphemeralProvisioner(sql_executor=rec)
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="per_agent_ephemeral",
        namespace_template="jig_eph_{ticket_id}",
        cleanup_on_success="drop",
        cleanup_on_failure="archive",
    )
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=False)
    # Archive renames the database; we don't pin the timestamp suffix
    # but assert the SQL pattern holds.
    assert len(rec.calls) == 1
    assert rec.calls[0].startswith(
        "ALTER DATABASE jig_eph_t_001 RENAME TO archived_jig_eph_t_001"
    )


@pytest.mark.asyncio
async def test_postgres_db_ephemeral_cleanup_keep_no_op() -> None:
    rec = _Recorder()
    p = PostgresDbEphemeralProvisioner(sql_executor=rec)
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="per_agent_ephemeral",
        namespace_template="jig_eph_{ticket_id}",
        cleanup_on_success="keep",
        cleanup_on_failure="keep",
    )
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=True)
    await p.cleanup(svc, agent_id="dev", ticket_id="t-001", success=False)
    assert rec.calls == []


# ---------------------------------------------------------------------------
# Registry / dispatcher: per_agent_ephemeral kinds wire correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_dispatches_sqlite_ephemeral(tmp_path: Path) -> None:
    """Registry routes (sqlite, per_agent_ephemeral) → SqliteEphemeralProvisioner."""
    reg = ProvisioningRegistry(project_root=tmp_path)
    m = DevManifest(
        services=[
            ManifestService(
                id="ephem",
                kind="sqlite",
                strategy="per_agent_ephemeral",
                namespace_template="agent_{ticket_id}",
            ),
        ],
        connection_string_templates={"ephem": ""},
    )
    out = await provision_agent_namespace(
        m, agent_id="dev", ticket_id="t-001", registry=reg
    )
    assert "ephem" in out
    assert "agent_t_001.db" in out["ephem"]
    db_path = (
        tmp_path / ".jig" / "dev" / "ephemeral" / "ephem"
        / "agent_t_001.db"
    )
    assert db_path.is_file()


@pytest.mark.asyncio
async def test_registry_dispatches_postgres_ephemeral(tmp_path: Path) -> None:
    """Registry routes (postgres, per_agent_ephemeral) → PostgresDbEphemeralProvisioner."""
    rec = _Recorder()
    reg = ProvisioningRegistry(
        project_root=tmp_path,
        postgres_db_ephemeral_sql_executor=rec,
    )
    m = DevManifest(
        services=[
            ManifestService(
                id="main-db",
                kind="postgres",
                strategy="per_agent_ephemeral",
                namespace_template="jig_eph_{ticket_id}",
            ),
        ],
        connection_string_templates={"main-db": ""},
    )
    out = await provision_agent_namespace(
        m, agent_id="dev", ticket_id="t-001", registry=reg
    )
    assert "main-db" in out
    assert "jig_eph_t_001" in out["main-db"]
    assert rec.calls == ["CREATE DATABASE jig_eph_t_001"]


@pytest.mark.asyncio
async def test_cleanup_dispatches_sqlite_ephemeral(tmp_path: Path) -> None:
    reg = ProvisioningRegistry(project_root=tmp_path)
    m = DevManifest(
        services=[
            ManifestService(
                id="ephem",
                kind="sqlite",
                strategy="per_agent_ephemeral",
                namespace_template="agent_{ticket_id}",
                cleanup_on_success="drop",
            ),
        ],
        connection_string_templates={"ephem": ""},
    )
    await provision_agent_namespace(
        m, agent_id="dev", ticket_id="t-001", registry=reg
    )
    db_path = (
        tmp_path / ".jig" / "dev" / "ephemeral" / "ephem"
        / "agent_t_001.db"
    )
    assert db_path.is_file()
    await cleanup_agent_namespace(
        m,
        agent_id="dev",
        ticket_id="t-001",
        success=True,
        registry=reg,
    )
    assert not db_path.is_file()


def test_ephemeral_provisioner_base_class_signature() -> None:
    """The base class declares the expected async API surface."""
    assert hasattr(EphemeralProvisioner, "provision")
    assert hasattr(EphemeralProvisioner, "cleanup")
