"""Per-agent namespace provisioning hooks (Track E MVP deliverable 3).

Per ``docs/dev-environment/design.md`` §"Agent spawn lifecycle (with
provisioning)": before each agent spawn, the orchestrator provisions
the per-service namespace (Postgres CREATE SCHEMA, NATS subject
prefix, etc.) and hands the resulting connection-string map to the
SDK as env vars. After the agent finishes, it cleans up per the
``cleanup_on_success`` / ``cleanup_on_failure`` policy.

For MVP scope ship four kinds — the cheap services per Track E MVP
scope:

- ``postgres`` — CREATE SCHEMA / DROP SCHEMA / ALTER SCHEMA RENAME
  via an injectable async SQL-execute callable so tests don't need
  a live Postgres. Production wiring passes an asyncpg-backed
  callable; tests pass an in-memory recorder.
- ``nats`` — pure prefix; no I/O. The connection-string template
  carries the prefix and the agent's code reads from env.
- ``redis`` — pure prefix; no I/O.
- ``s3`` — pure prefix; no I/O.

Per-agent ephemeral provisioning (a per-file SQLite, a fresh
Postgres DB per agent, etc.) is Final scope. The schema parses
those strategies but the dispatcher raises ``NotImplementedError``
if asked to provision them.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from jig.schemas.dev_env import DevManifest, ManifestService

if TYPE_CHECKING:
    from jig.dev_env.ephemeral import EphemeralProvisioner

__all__ = [
    "NamespaceProvisioner",
    "NatsSubjectPrefixProvisioner",
    "PostgresSchemaProvisioner",
    "ProvisioningRegistry",
    "RedisKeyPrefixProvisioner",
    "S3BucketPrefixProvisioner",
    "SqlExecutor",
    "cleanup_agent_namespace",
    "provision_agent_namespace",
    "render_namespace",
]

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sanitization + template substitution
# ---------------------------------------------------------------------------


def _sanitize(value: str) -> str:
    """Lowercase + replace non-alphanumeric with underscores.

    Postgres schema names accept ``a-z0-9_`` (anything else needs
    quoting we'd rather avoid); NATS subjects accept dots + alnum;
    Redis / S3 prefixes are friendlier still. Lowest-common-denominator
    is alphanumeric + underscore.
    """
    out: list[str] = []
    for ch in value:
        if ch.isalnum():
            out.append(ch.lower())
        else:
            out.append("_")
    return "".join(out)


def render_namespace(
    template: str,
    *,
    agent_id: str,
    ticket_id: str,
    epic_id: str | None = None,
) -> str:
    """Substitute {agent_id} / {ticket_id} / {epic_id} into ``template``.

    Each id is sanitized to ``[a-z0-9_]+`` so the rendered namespace is
    safe for use as a Postgres schema identifier without quoting.
    """
    return template.format(
        agent_id=_sanitize(agent_id),
        ticket_id=_sanitize(ticket_id),
        epic_id=_sanitize(epic_id) if epic_id else "",
    )


def _render_connection_string(
    template: str,
    *,
    namespace: str,
    agent_id: str,
    ticket_id: str,
    epic_id: str | None,
) -> str:
    """Substitute {namespace} (and the id placeholders) into a URL template."""
    return template.format(
        namespace=namespace,
        agent_id=_sanitize(agent_id),
        ticket_id=_sanitize(ticket_id),
        epic_id=_sanitize(epic_id) if epic_id else "",
    )


# ---------------------------------------------------------------------------
# Base + concrete provisioners
# ---------------------------------------------------------------------------


SqlExecutor = Callable[[str], Awaitable[None]]


class NamespaceProvisioner:
    """Base — concrete subclasses implement provision/cleanup per kind.

    Pure-prefix kinds (NATS / Redis / S3) keep ``provision`` as a
    namespace-prefix emitter (no I/O). Postgres adds an injectable
    SQL-execute callable so tests don't need a live database.
    """

    kind: ClassVar[str] = ""

    async def provision(
        self, service: ManifestService, namespace: str
    ) -> None:
        """Make the namespace exist on the underlying service.

        Pure-prefix kinds no-op. Postgres runs ``CREATE SCHEMA IF NOT
        EXISTS``. Idempotent — calling twice is fine.
        """
        return None

    async def cleanup(
        self,
        service: ManifestService,
        namespace: str,
        success: bool,
    ) -> None:
        """Honor the cleanup_on_success / cleanup_on_failure policy.

        ``keep`` no-ops. ``drop`` issues a destructive removal.
        ``archive`` renames the namespace so the operator can inspect
        it later (Postgres ``ALTER SCHEMA`` rename; pure-prefix kinds
        no-op since they have no resources to retain).
        """
        return None


class PostgresSchemaProvisioner(NamespaceProvisioner):
    """Provision per-agent Postgres schemas via injected SQL executor."""

    kind: ClassVar[str] = "postgres"

    def __init__(self, sql_executor: SqlExecutor | None = None) -> None:
        self._sql = sql_executor

    async def provision(
        self, service: ManifestService, namespace: str
    ) -> None:
        if self._sql is None:
            # MVP scope: production wiring lands later. Logging keeps
            # the path observable; we still return so the env-var
            # injection map is built, and the agent will fail fast on
            # first DB call if no schema exists.
            _logger.info(
                "PostgresSchemaProvisioner.provision: no sql_executor "
                "wired (service=%s namespace=%s) — skipping",
                service.id,
                namespace,
            )
            return
        await self._sql(f"CREATE SCHEMA IF NOT EXISTS {namespace}")

    async def cleanup(
        self,
        service: ManifestService,
        namespace: str,
        success: bool,
    ) -> None:
        policy = (
            service.cleanup_on_success if success else service.cleanup_on_failure
        )
        if policy == "keep":
            return
        if self._sql is None:
            _logger.info(
                "PostgresSchemaProvisioner.cleanup: no sql_executor "
                "wired (service=%s namespace=%s policy=%s) — skipping",
                service.id,
                namespace,
                policy,
            )
            return
        if policy == "drop":
            await self._sql(f"DROP SCHEMA IF EXISTS {namespace} CASCADE")
        elif policy == "archive":
            archived = f"archived_{namespace}"
            await self._sql(
                f"ALTER SCHEMA {namespace} RENAME TO {archived}"
            )


class NatsSubjectPrefixProvisioner(NamespaceProvisioner):
    """Pure-prefix; no I/O. The subject prefix lives in the URL."""

    kind: ClassVar[str] = "nats"


class RedisKeyPrefixProvisioner(NamespaceProvisioner):
    """Pure-prefix; no I/O. The key prefix lives in the URL."""

    kind: ClassVar[str] = "redis"


class S3BucketPrefixProvisioner(NamespaceProvisioner):
    """Pure-prefix; no I/O. The bucket prefix lives in the URL."""

    kind: ClassVar[str] = "s3"


# ---------------------------------------------------------------------------
# Registry + dispatcher
# ---------------------------------------------------------------------------


class ProvisioningRegistry:
    """Map ``(kind, strategy)`` to a configured provisioner.

    Built once per orchestrator startup with whatever wiring the
    deployment requires (e.g. an asyncpg-backed Postgres callable in
    production; an in-memory recorder in tests).

    For ``shared_namespaced`` services the registry returns a
    :class:`NamespaceProvisioner`; for ``per_agent_ephemeral`` services
    it returns an
    :class:`~jig.dev_env.ephemeral.EphemeralProvisioner`. Track E Final
    extended the registry with the ephemeral path; ``operator_supplied``
    remains unsupported (operator owns the connection string out-of-
    band per the design).
    """

    def __init__(
        self,
        *,
        postgres_sql_executor: SqlExecutor | None = None,
        project_root: Path | None = None,
        postgres_db_ephemeral_sql_executor: SqlExecutor | None = None,
    ) -> None:
        self._provisioners: dict[str, NamespaceProvisioner] = {
            "postgres": PostgresSchemaProvisioner(postgres_sql_executor),
            "nats": NatsSubjectPrefixProvisioner(),
            "redis": RedisKeyPrefixProvisioner(),
            "s3": S3BucketPrefixProvisioner(),
        }
        # Local import keeps the MVP shared_namespaced surface free of
        # the ephemeral module's own imports (and avoids a cycle if a
        # future ephemeral kind ever wants to call the dispatcher).
        from jig.dev_env.ephemeral import (
            PostgresDbEphemeralProvisioner,
            SqliteEphemeralProvisioner,
        )

        # The SQLite ephemeral provisioner needs a project_root to know
        # where to write the per-agent files. When the registry is built
        # without one (the MVP-style call site), the ephemeral SQLite
        # entry stays unwired and the dispatcher skips ephemeral
        # services with kind=sqlite — same shape as an unknown kind.
        ephemeral: dict[str, "EphemeralProvisioner"] = {}
        if project_root is not None:
            ephemeral["sqlite"] = SqliteEphemeralProvisioner(
                project_root=project_root
            )
        ephemeral["postgres"] = PostgresDbEphemeralProvisioner(
            sql_executor=postgres_db_ephemeral_sql_executor,
        )
        self._ephemeral: dict[str, "EphemeralProvisioner"] = ephemeral

    def get(self, kind: str) -> NamespaceProvisioner | None:
        """Return the shared_namespaced provisioner for ``kind``.

        Unknown kinds are skipped silently — the orchestrator-side
        wrapper logs them but doesn't fail the spawn (the agent still
        runs, just without that service's connection string in env).
        """
        return self._provisioners.get(kind)

    def get_ephemeral(self, kind: str) -> "EphemeralProvisioner | None":
        """Return the per_agent_ephemeral provisioner for ``kind`` or ``None``."""
        return self._ephemeral.get(kind)

    def register(
        self, kind: str, provisioner: NamespaceProvisioner
    ) -> None:
        """Register or replace a shared_namespaced provisioner."""
        self._provisioners[kind] = provisioner

    def register_ephemeral(
        self, kind: str, provisioner: "EphemeralProvisioner"
    ) -> None:
        """Register or replace a per_agent_ephemeral provisioner."""
        self._ephemeral[kind] = provisioner


# ---------------------------------------------------------------------------
# Orchestrator entry points
# ---------------------------------------------------------------------------


async def provision_agent_namespace(
    manifest: DevManifest,
    *,
    agent_id: str,
    ticket_id: str,
    epic_id: str | None = None,
    registry: ProvisioningRegistry | None = None,
    project_root: Path | None = None,
) -> dict[str, str]:
    """Provision one namespace per service in ``manifest``; return URLs.

    Returns ``{service_id: connection_string}`` ready for env-var
    injection. Dispatches per service:

    - ``shared_namespaced`` → :class:`NamespaceProvisioner`; URL built
      from the manifest's per-service ``connection_string_template``.
    - ``per_agent_ephemeral`` → :class:`EphemeralProvisioner`; URL
      returned directly by the provisioner (each ephemeral instance
      knows its own connection string).
    - ``operator_supplied`` → skipped (operator manages the connection
      string out-of-band).

    Services whose kind has no registered provisioner are skipped
    silently — the agent still runs, just without that service's
    connection string in env. Exceptions raised by individual
    provisioners propagate; the orchestrator's wrapper decides whether
    to fail the spawn (per the design's HEALTH-CHECK step).

    When ``registry`` is None, an auto-built registry is created with
    ``project_root`` (when supplied) so per-agent-ephemeral services that
    need on-disk state (SQLite) are registered automatically. Without a
    ``project_root`` the auto-built registry leaves the SQLite ephemeral
    provisioner unwired (kept for backward-compat with callers that
    explicitly opt out of ephemeral SQLite).
    """
    registry = registry or ProvisioningRegistry(project_root=project_root)
    out: dict[str, str] = {}
    for service in manifest.services:
        if service.strategy == "shared_namespaced":
            provisioner = registry.get(service.kind)
            if provisioner is None:
                _logger.info(
                    "skip provisioning service=%s kind=%s — "
                    "no registered provisioner",
                    service.id,
                    service.kind,
                )
                continue
            namespace = render_namespace(
                service.namespace_template,
                agent_id=agent_id,
                ticket_id=ticket_id,
                epic_id=epic_id,
            )
            await provisioner.provision(service, namespace)
            template = manifest.connection_string_templates.get(service.id, "")
            if template:
                out[service.id] = _render_connection_string(
                    template,
                    namespace=namespace,
                    agent_id=agent_id,
                    ticket_id=ticket_id,
                    epic_id=epic_id,
                )
        elif service.strategy == "per_agent_ephemeral":
            ephemeral = registry.get_ephemeral(service.kind)
            if ephemeral is None:
                _logger.info(
                    "skip provisioning service=%s kind=%s strategy=%s — "
                    "no registered ephemeral provisioner",
                    service.id,
                    service.kind,
                    service.strategy,
                )
                continue
            url = await ephemeral.provision(
                service,
                agent_id=agent_id,
                ticket_id=ticket_id,
                epic_id=epic_id,
            )
            if url:
                out[service.id] = url
        else:
            # operator_supplied: nothing to do; the operator's existing
            # service is reachable via whatever they configured.
            _logger.info(
                "skip provisioning service=%s strategy=%s — "
                "operator-managed",
                service.id,
                service.strategy,
            )
            continue
    return out


async def cleanup_agent_namespace(
    manifest: DevManifest,
    *,
    agent_id: str,
    ticket_id: str,
    success: bool,
    epic_id: str | None = None,
    registry: ProvisioningRegistry | None = None,
    project_root: Path | None = None,
) -> None:
    """Clean up per-service namespaces honoring the success / failure policy.

    Best-effort: each per-service cleanup runs inside its own
    try/except; failures are logged but not re-raised (the orchestrator
    is already in the "agent finished" path and shouldn't crash on
    cleanup errors per ``docs/dev-environment/design.md`` §"Cleanup
    discipline" failure mode 1).

    See :func:`provision_agent_namespace` for the ``project_root`` /
    auto-built-registry contract — symmetric here so ephemeral cleanups
    can find their on-disk state.
    """
    registry = registry or ProvisioningRegistry(project_root=project_root)
    for service in manifest.services:
        if service.strategy == "shared_namespaced":
            provisioner = registry.get(service.kind)
            if provisioner is None:
                continue
            namespace = render_namespace(
                service.namespace_template,
                agent_id=agent_id,
                ticket_id=ticket_id,
                epic_id=epic_id,
            )
            try:
                await provisioner.cleanup(service, namespace, success)
            except Exception:
                _logger.warning(
                    "cleanup failed for service=%s namespace=%s success=%s",
                    service.id,
                    namespace,
                    success,
                    exc_info=True,
                )
        elif service.strategy == "per_agent_ephemeral":
            ephemeral = registry.get_ephemeral(service.kind)
            if ephemeral is None:
                continue
            try:
                await ephemeral.cleanup(
                    service,
                    agent_id=agent_id,
                    ticket_id=ticket_id,
                    success=success,
                    epic_id=epic_id,
                )
            except Exception:
                _logger.warning(
                    "ephemeral cleanup failed for service=%s success=%s",
                    service.id,
                    success,
                    exc_info=True,
                )
        # operator_supplied: nothing to clean up.
