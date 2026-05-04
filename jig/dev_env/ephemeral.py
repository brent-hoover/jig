"""Per-agent ephemeral provisioners + inspection helpers (Track E Final).

Per ``docs/dev-environment/design.md`` §"Provisioning strategies", the
``per_agent_ephemeral`` strategy is the strong-isolation escape hatch
for services that don't namespace cleanly across processes. Final scope
ships two concrete implementations:

- :class:`SqliteEphemeralProvisioner` — provisions a fresh SQLite file
  per (agent, ticket) pair under ``.jig/dev/ephemeral/<service_id>/``.
  Cleanup honors the per-service ``cleanup_on_success`` /
  ``cleanup_on_failure`` policy: ``drop`` deletes the file, ``archive``
  moves it under ``.jig/dev/archive/`` with a timestamped name for
  later operator inspection, ``keep`` leaves it in place.
- :class:`PostgresDbEphemeralProvisioner` — uses an injectable async
  SQL-execute callable (mirroring the MVP shared-namespaced Postgres
  provisioner pattern) to run ``CREATE DATABASE`` + ``DROP DATABASE``
  per agent. The injectable seam keeps tests fast and lets production
  wire any backend (asyncpg, psycopg, ...).

The base :class:`EphemeralProvisioner` declares the contract used by
the dispatcher in :mod:`jig.dev_env.provisioning`. Other ephemeral
kinds (in-process Redis etc.) are out of scope for Final.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from jig.dev_env.provisioning import SqlExecutor, render_namespace
from jig.safe_path import validate_safe_path_segment
from jig.schemas.dev_env import ManifestService

__all__ = [
    "EphemeralInstance",
    "EphemeralProvisioner",
    "InspectResult",
    "PostgresDbEphemeralProvisioner",
    "SqliteEphemeralProvisioner",
    "drop_ephemeral_instance",
    "ephemeral_archive_dir",
    "ephemeral_root",
    "inspect_ephemeral_instance",
    "list_ephemeral_instances",
]

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def ephemeral_root(project_root: Path) -> Path:
    """``.jig/dev/ephemeral/`` — root for per-agent ephemeral SQLite files."""
    return project_root / ".jig" / "dev" / "ephemeral"


def ephemeral_archive_dir(project_root: Path) -> Path:
    """``.jig/dev/archive/`` — destination for archived (failed-run) SQLite files."""
    return project_root / ".jig" / "dev" / "archive"


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class EphemeralProvisioner:
    """Base contract for per-agent ephemeral provisioners.

    Concrete subclasses override :meth:`provision` (returns a connection
    string) and :meth:`cleanup` (honors the per-service cleanup policy).
    Subclasses MUST be safe to call ``cleanup`` against a non-existent
    instance (best-effort cleanup per the design's "failure-mode 1"
    discussion — orphans accumulate, the sweeper handles them).
    """

    kind: ClassVar[str] = ""

    async def provision(
        self,
        service: ManifestService,
        *,
        agent_id: str,
        ticket_id: str,
        epic_id: str | None = None,
    ) -> str:
        """Provision a fresh ephemeral instance; return its connection string."""
        raise NotImplementedError

    async def cleanup(
        self,
        service: ManifestService,
        *,
        agent_id: str,
        ticket_id: str,
        success: bool,
        epic_id: str | None = None,
    ) -> None:
        """Honor the cleanup_on_success / cleanup_on_failure policy.

        Best-effort: failures are logged but never re-raised — cleanup
        runs in the orchestrator's "agent finished" path and a crash
        here would mask the agent's actual outcome.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# SQLite — per-file ephemeral
# ---------------------------------------------------------------------------


class SqliteEphemeralProvisioner(EphemeralProvisioner):
    """One SQLite file per (agent, ticket) under ``.jig/dev/ephemeral/<svc>/``."""

    kind: ClassVar[str] = "sqlite"

    def __init__(self, *, project_root: Path) -> None:
        self._root = project_root

    def _db_path(
        self,
        service: ManifestService,
        *,
        agent_id: str,
        ticket_id: str,
        epic_id: str | None = None,
    ) -> Path:
        # ``service.id`` flows from manifest YAML into a directory
        # name; defense in depth against a manifest bypass producing
        # a malformed id. ``namespace`` is rendered through
        # ``render_namespace`` which already sanitizes the agent /
        # ticket / epic ids to ``[a-z0-9_]+``, so the .db filename
        # is path-safe by construction.
        validate_safe_path_segment(service.id, "service.id")
        namespace = render_namespace(
            service.namespace_template,
            agent_id=agent_id,
            ticket_id=ticket_id,
            epic_id=epic_id,
        )
        return ephemeral_root(self._root) / service.id / f"{namespace}.db"

    async def provision(
        self,
        service: ManifestService,
        *,
        agent_id: str,
        ticket_id: str,
        epic_id: str | None = None,
    ) -> str:
        db_path = self._db_path(
            service, agent_id=agent_id, ticket_id=ticket_id, epic_id=epic_id
        )
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Touch the file so the agent's first connect doesn't have to
        # create it; mirrors the design's "fail-fast at HEALTH-CHECK"
        # principle (existence check at provision time).
        db_path.touch(exist_ok=True)
        return f"sqlite:///{db_path}"

    async def cleanup(
        self,
        service: ManifestService,
        *,
        agent_id: str,
        ticket_id: str,
        success: bool,
        epic_id: str | None = None,
    ) -> None:
        policy = (
            service.cleanup_on_success if success else service.cleanup_on_failure
        )
        if policy == "keep":
            return
        db_path = self._db_path(
            service, agent_id=agent_id, ticket_id=ticket_id, epic_id=epic_id
        )
        if not db_path.is_file():
            # Best-effort: nothing to do (orphan or already cleaned).
            return
        try:
            if policy == "drop":
                db_path.unlink()
            elif policy == "archive":
                arc_dir = ephemeral_archive_dir(self._root)
                arc_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                archived_name = (
                    f"{ts}_{service.id}_{agent_id}_{ticket_id}_"
                    f"{db_path.stem}.db.bak"
                )
                shutil.move(str(db_path), str(arc_dir / archived_name))
        except OSError:
            _logger.warning(
                "SqliteEphemeralProvisioner.cleanup failed for %s "
                "(policy=%s)",
                db_path,
                policy,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Postgres — per-DB ephemeral
# ---------------------------------------------------------------------------


class PostgresDbEphemeralProvisioner(EphemeralProvisioner):
    """One Postgres database per (agent, ticket) via injectable SQL executor.

    The injectable seam mirrors the MVP shared-namespaced Postgres
    provisioner: tests pass an in-memory recorder, production wires an
    asyncpg-backed callable. ``CREATE DATABASE`` cannot run inside a
    transaction, so the callable should auto-commit.
    """

    kind: ClassVar[str] = "postgres"

    # URL template the provisioner returns when asked for a connection
    # string. Final scope: a single localhost template (overridable via
    # ``url_template`` ctor arg). The agent's code reads from env so
    # the host / port / credentials are deployment concerns.
    _DEFAULT_URL_TEMPLATE = "postgresql://jig:jig@localhost:5432/{database}"

    def __init__(
        self,
        *,
        sql_executor: SqlExecutor | None = None,
        url_template: str | None = None,
    ) -> None:
        self._sql = sql_executor
        self._url_template = url_template or self._DEFAULT_URL_TEMPLATE

    def _db_name(
        self,
        service: ManifestService,
        *,
        agent_id: str,
        ticket_id: str,
        epic_id: str | None = None,
    ) -> str:
        return render_namespace(
            service.namespace_template,
            agent_id=agent_id,
            ticket_id=ticket_id,
            epic_id=epic_id,
        )

    async def provision(
        self,
        service: ManifestService,
        *,
        agent_id: str,
        ticket_id: str,
        epic_id: str | None = None,
    ) -> str:
        db_name = self._db_name(
            service, agent_id=agent_id, ticket_id=ticket_id, epic_id=epic_id
        )
        if self._sql is None:
            _logger.info(
                "PostgresDbEphemeralProvisioner.provision: no sql_executor "
                "wired (service=%s db=%s) — skipping CREATE DATABASE",
                service.id,
                db_name,
            )
        else:
            await self._sql(f"CREATE DATABASE {db_name}")
        return self._url_template.format(database=db_name)

    async def cleanup(
        self,
        service: ManifestService,
        *,
        agent_id: str,
        ticket_id: str,
        success: bool,
        epic_id: str | None = None,
    ) -> None:
        policy = (
            service.cleanup_on_success if success else service.cleanup_on_failure
        )
        if policy == "keep":
            return
        if self._sql is None:
            _logger.info(
                "PostgresDbEphemeralProvisioner.cleanup: no sql_executor "
                "wired (service=%s policy=%s) — skipping",
                service.id,
                policy,
            )
            return
        db_name = self._db_name(
            service, agent_id=agent_id, ticket_id=ticket_id, epic_id=epic_id
        )
        try:
            if policy == "drop":
                await self._sql(f"DROP DATABASE IF EXISTS {db_name}")
            elif policy == "archive":
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                archived = f"archived_{db_name}_{ts}"
                await self._sql(
                    f"ALTER DATABASE {db_name} RENAME TO {archived}"
                )
        except Exception:
            _logger.warning(
                "PostgresDbEphemeralProvisioner.cleanup failed "
                "(service=%s policy=%s)",
                service.id,
                policy,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# Operator-facing inspection — drives the ``jig dev ephemeral`` CLI
# ---------------------------------------------------------------------------


class EphemeralInstance(BaseModel):
    """One ephemeral instance row surfaced by ``list_ephemeral_instances``.

    The composite ``id`` is ``<service_id>:<namespace>`` so the operator
    can reference it deterministically from the CLI (no spaces, all
    safe shell chars) — same shape as ``OrphanedNamespace.id``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    service_id: str = Field(..., min_length=1)
    namespace: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    size_bytes: int = Field(..., ge=0)


class InspectResult(BaseModel):
    """One ``(table_name, row_count)`` row for the inspect output."""

    model_config = ConfigDict(extra="forbid")

    table: str = Field(..., min_length=1)
    rows: int = Field(..., ge=0)


def _instance_id(service_id: str, namespace: str) -> str:
    return f"{service_id}:{namespace}"


def list_ephemeral_instances(project_root: Path) -> list[EphemeralInstance]:
    """Walk ``.jig/dev/ephemeral/`` and return one row per SQLite file.

    Postgres ephemeral instances aren't enumerable from disk (they live
    in the postgres cluster); a Postgres listing would require querying
    ``pg_database`` via the same injectable executor pattern the
    provisioner uses. For Final scope SQLite is the listing surface;
    the operator inspects Postgres ephemeral DBs via ``psql`` directly.
    """
    out: list[EphemeralInstance] = []
    root = ephemeral_root(project_root)
    if not root.is_dir():
        return out
    for service_dir in sorted(root.iterdir()):
        if not service_dir.is_dir():
            continue
        service_id = service_dir.name
        for db_path in sorted(service_dir.glob("*.db")):
            namespace = db_path.stem
            try:
                size = db_path.stat().st_size
            except OSError:
                continue
            out.append(
                EphemeralInstance(
                    id=_instance_id(service_id, namespace),
                    service_id=service_id,
                    namespace=namespace,
                    kind="sqlite",
                    path=str(db_path),
                    size_bytes=size,
                )
            )
    return out


def _split_instance_id(instance_id: str) -> tuple[str, str]:
    if ":" not in instance_id:
        raise ValueError(
            f"ephemeral instance id {instance_id!r} must be "
            "<service_id>:<namespace>"
        )
    service_id, _, namespace = instance_id.partition(":")
    if not service_id or not namespace:
        raise ValueError(
            f"ephemeral instance id {instance_id!r} must be "
            "<service_id>:<namespace>"
        )
    return service_id, namespace


def _find_instance(
    project_root: Path, instance_id: str
) -> EphemeralInstance | None:
    rows = list_ephemeral_instances(project_root)
    return next((r for r in rows if r.id == instance_id), None)


def inspect_ephemeral_instance(
    project_root: Path, instance_id: str
) -> list[InspectResult]:
    """Return per-table row counts for the named SQLite instance.

    Raises ``FileNotFoundError`` when the instance id doesn't resolve
    to a tracked file. An empty SQLite file (no tables created yet)
    returns an empty list — the CLI wraps that into a friendly
    ``(no tables)`` message.
    """
    inst = _find_instance(project_root, instance_id)
    if inst is None:
        raise FileNotFoundError(
            f"ephemeral instance {instance_id!r} not found under "
            f"{ephemeral_root(project_root)}"
        )
    out: list[InspectResult] = []
    conn = sqlite3.connect(inst.path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [row[0] for row in cur.fetchall()]
        for table in tables:
            # Use parameterized identifiers via quoted names — sqlite
            # doesn't support bound identifiers, so we wrap in double
            # quotes after a sanity check that the name came from
            # sqlite_master (already trusted).
            count_cur = conn.execute(f'SELECT COUNT(*) FROM "{table}"')
            (count,) = count_cur.fetchone()
            out.append(InspectResult(table=table, rows=int(count)))
    finally:
        conn.close()
    return out


def drop_ephemeral_instance(
    project_root: Path, instance_id: str
) -> bool:
    """Operator override — drop a SQLite ephemeral instance directly.

    Returns ``True`` iff the instance was found + removed; ``False``
    when the id doesn't resolve. Bypasses the orchestrator hook (which
    only fires on agent completion) — used when the operator wants to
    reclaim disk before a long-running ticket finishes, or after the
    orchestrator missed a cleanup.
    """
    inst = _find_instance(project_root, instance_id)
    if inst is None:
        return False
    try:
        Path(inst.path).unlink()
    except OSError:
        _logger.warning(
            "drop_ephemeral_instance failed for %s",
            inst.path,
            exc_info=True,
        )
        return False
    return True
