"""Tests for the per-agent namespace provisioning hooks (Track E MVP)."""

from __future__ import annotations

import pytest

from jig.dev_env.provisioning import (
    NatsSubjectPrefixProvisioner,
    PostgresSchemaProvisioner,
    ProvisioningRegistry,
    RedisKeyPrefixProvisioner,
    S3BucketPrefixProvisioner,
    cleanup_agent_namespace,
    provision_agent_namespace,
    render_namespace,
)
from jig.schemas.dev_env import DevManifest, ManifestService


# ---------------------------------------------------------------------------
# Template substitution + sanitization
# ---------------------------------------------------------------------------


def test_render_namespace_substitutes_all_placeholders():
    out = render_namespace(
        "agent_{agent_id}_ticket_{ticket_id}_epic_{epic_id}",
        agent_id="dev",
        ticket_id="t-001",
        epic_id="catalog-ingest",
    )
    assert out == "agent_dev_ticket_t_001_epic_catalog_ingest"


def test_render_namespace_lowercases_and_swaps_specials():
    """Non-alnum chars become underscores; uppercase becomes lower."""
    out = render_namespace(
        "ns_{ticket_id}",
        agent_id="x",
        ticket_id="T-001-Foo!Bar",
    )
    assert out == "ns_t_001_foo_bar"


def test_render_namespace_handles_missing_epic():
    out = render_namespace(
        "ns_{ticket_id}",
        agent_id="x",
        ticket_id="t-1",
    )
    assert out == "ns_t_1"


# ---------------------------------------------------------------------------
# In-memory SQL recorder used across the Postgres-flavored tests
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(self, sql: str) -> None:
        self.calls.append(sql)


# ---------------------------------------------------------------------------
# PostgresSchemaProvisioner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_postgres_provision_runs_create_schema():
    rec = _Recorder()
    p = PostgresSchemaProvisioner(rec)
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="shared_namespaced",
        namespace_template="agent_{ticket_id}",
    )
    await p.provision(svc, "agent_t_001")
    assert rec.calls == ["CREATE SCHEMA IF NOT EXISTS agent_t_001"]


@pytest.mark.asyncio
async def test_postgres_provision_no_executor_does_not_crash():
    """MVP fallback — no live DB; provisioning is a no-op when unwired."""
    p = PostgresSchemaProvisioner()
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="shared_namespaced",
        namespace_template="agent_{ticket_id}",
    )
    await p.provision(svc, "agent_t_001")  # no raise


@pytest.mark.asyncio
async def test_postgres_cleanup_drop_on_success():
    rec = _Recorder()
    p = PostgresSchemaProvisioner(rec)
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="shared_namespaced",
        namespace_template="agent_{ticket_id}",
        cleanup_on_success="drop",
        cleanup_on_failure="archive",
    )
    await p.cleanup(svc, "agent_t_001", success=True)
    assert rec.calls == ["DROP SCHEMA IF EXISTS agent_t_001 CASCADE"]


@pytest.mark.asyncio
async def test_postgres_cleanup_archive_on_failure():
    rec = _Recorder()
    p = PostgresSchemaProvisioner(rec)
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="shared_namespaced",
        namespace_template="agent_{ticket_id}",
        cleanup_on_success="drop",
        cleanup_on_failure="archive",
    )
    await p.cleanup(svc, "agent_t_001", success=False)
    assert rec.calls == ["ALTER SCHEMA agent_t_001 RENAME TO archived_agent_t_001"]


@pytest.mark.asyncio
async def test_postgres_cleanup_keep_does_nothing():
    rec = _Recorder()
    p = PostgresSchemaProvisioner(rec)
    svc = ManifestService(
        id="main-db",
        kind="postgres",
        strategy="shared_namespaced",
        namespace_template="agent_{ticket_id}",
        cleanup_on_success="keep",
        cleanup_on_failure="keep",
    )
    await p.cleanup(svc, "agent_t_001", success=True)
    await p.cleanup(svc, "agent_t_001", success=False)
    assert rec.calls == []


# ---------------------------------------------------------------------------
# Pure-prefix provisioners — no I/O
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pure_prefix_provisioners_no_op():
    svc = ManifestService(
        id="x",
        kind="nats",
        strategy="shared_namespaced",
        namespace_template="ns",
    )
    for cls in (
        NatsSubjectPrefixProvisioner,
        RedisKeyPrefixProvisioner,
        S3BucketPrefixProvisioner,
    ):
        p = cls()
        await p.provision(svc, "ns")
        await p.cleanup(svc, "ns", success=True)
        await p.cleanup(svc, "ns", success=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_default_kinds():
    reg = ProvisioningRegistry()
    assert isinstance(reg.get("postgres"), PostgresSchemaProvisioner)
    assert isinstance(reg.get("nats"), NatsSubjectPrefixProvisioner)
    assert isinstance(reg.get("redis"), RedisKeyPrefixProvisioner)
    assert isinstance(reg.get("s3"), S3BucketPrefixProvisioner)


def test_registry_unknown_kind_returns_none():
    assert ProvisioningRegistry().get("opensearch") is None


def test_registry_register_overrides_default():
    reg = ProvisioningRegistry()
    custom = NatsSubjectPrefixProvisioner()
    reg.register("nats", custom)
    assert reg.get("nats") is custom


# ---------------------------------------------------------------------------
# provision_agent_namespace dispatcher
# ---------------------------------------------------------------------------


def _manifest() -> tuple[DevManifest, _Recorder, ProvisioningRegistry]:
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    m = DevManifest(
        services=[
            ManifestService(
                id="main-db",
                kind="postgres",
                strategy="shared_namespaced",
                namespace_template="agent_{ticket_id}",
            ),
            ManifestService(
                id="event-bus",
                kind="nats",
                strategy="shared_namespaced",
                namespace_template="agent.{ticket_id}",
            ),
            ManifestService(
                id="cache",
                kind="redis",
                strategy="shared_namespaced",
                namespace_template="agent_{ticket_id}",
            ),
            ManifestService(
                id="objects",
                kind="s3",
                strategy="shared_namespaced",
                namespace_template="agent-{ticket_id}-",
            ),
        ],
        connection_string_templates={
            "main-db": (
                "postgresql://jig:jig@localhost:5432/jigdev"
                "?options=-c%20search_path%3D{namespace}"
            ),
            "event-bus": "nats://localhost:4222?subject_prefix={namespace}",
            "cache": "redis://localhost:6379/0?key_prefix={namespace}",
            "objects": "s3://localhost:9000/jigdev?bucket_prefix={namespace}",
        },
    )
    return m, rec, reg


@pytest.mark.asyncio
async def test_provision_agent_namespace_returns_per_service_urls():
    m, rec, reg = _manifest()
    out = await provision_agent_namespace(
        m,
        agent_id="dev",
        ticket_id="t-001",
        registry=reg,
    )
    assert set(out) == {"main-db", "event-bus", "cache", "objects"}
    assert "search_path%3Dagent_t_001" in out["main-db"]
    # NATS template carries a literal dot ("agent.{ticket_id}");
    # sanitization rewrites only the placeholder values, so the dot
    # in the template survives but the ticket id ``t-001`` lowercases
    # and the dash becomes underscore.
    assert out["event-bus"].endswith("subject_prefix=agent.t_001")
    assert out["cache"].endswith("key_prefix=agent_t_001")
    assert out["objects"].endswith("bucket_prefix=agent-t_001-")
    # Postgres provisioner ran CREATE SCHEMA exactly once
    assert rec.calls == ["CREATE SCHEMA IF NOT EXISTS agent_t_001"]


@pytest.mark.asyncio
async def test_provision_agent_namespace_routes_strategies_correctly():
    """SQLite ephemeral lacks a project_root → skipped; operator_supplied
    is now wired (Block 2) and yields the templated connection string.
    Per-strategy routing keeps the dispatcher predictable.
    """
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    m = DevManifest(
        services=[
            ManifestService(
                id="ephemeral",
                kind="sqlite",
                strategy="per_agent_ephemeral",
                namespace_template="x",
            ),
            ManifestService(
                id="op",
                kind="postgres",
                strategy="operator_supplied",
                namespace_template="x",
            ),
        ],
        connection_string_templates={
            "ephemeral": "sqlite:///x/{namespace}.db",
            "op": "postgres://operator-managed/db",
        },
    )
    out = await provision_agent_namespace(
        m, agent_id="dev", ticket_id="t-1", registry=reg
    )
    # SQLite ephemeral skipped (no project_root on registry); op
    # surfaces the operator-supplied URL verbatim.
    assert out == {"op": "postgres://operator-managed/db"}
    # No CREATE SCHEMA / CREATE DATABASE for operator_supplied.
    assert rec.calls == []


@pytest.mark.asyncio
async def test_provision_agent_namespace_skips_unknown_kind():
    """An opensearch entry parses but no provisioner exists for it."""
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    m = DevManifest(
        services=[
            ManifestService(
                id="search",
                kind="opensearch",
                strategy="shared_namespaced",
                namespace_template="x",
            ),
        ],
        connection_string_templates={"search": "https://os/{namespace}"},
    )
    out = await provision_agent_namespace(
        m, agent_id="dev", ticket_id="t-1", registry=reg
    )
    assert out == {}


@pytest.mark.asyncio
async def test_provision_agent_namespace_omits_url_when_template_empty():
    """No connection_string_template means no URL in env, but provisioning still fires."""
    rec = _Recorder()
    reg = ProvisioningRegistry(postgres_sql_executor=rec)
    m = DevManifest(
        services=[
            ManifestService(
                id="main-db",
                kind="postgres",
                strategy="shared_namespaced",
                namespace_template="agent_{ticket_id}",
            ),
        ],
        connection_string_templates={"main-db": ""},
    )
    out = await provision_agent_namespace(
        m, agent_id="dev", ticket_id="t-1", registry=reg
    )
    assert out == {}
    assert rec.calls == ["CREATE SCHEMA IF NOT EXISTS agent_t_1"]


# ---------------------------------------------------------------------------
# cleanup_agent_namespace dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_agent_namespace_drops_on_success_archives_on_failure():
    m, rec, reg = _manifest()
    await cleanup_agent_namespace(
        m, agent_id="dev", ticket_id="t-001", success=True, registry=reg
    )
    assert rec.calls == ["DROP SCHEMA IF EXISTS agent_t_001 CASCADE"]
    rec.calls.clear()
    await cleanup_agent_namespace(
        m, agent_id="dev", ticket_id="t-001", success=False, registry=reg
    )
    assert rec.calls == ["ALTER SCHEMA agent_t_001 RENAME TO archived_agent_t_001"]


@pytest.mark.asyncio
async def test_cleanup_agent_namespace_swallows_provisioner_errors():
    """Cleanup is best-effort — a failure in one service must not propagate."""

    class _Boom(PostgresSchemaProvisioner):
        async def cleanup(self, service, namespace, success):  # type: ignore[override]
            raise RuntimeError("boom")

    reg = ProvisioningRegistry()
    reg.register("postgres", _Boom())
    m = DevManifest(
        services=[
            ManifestService(
                id="main-db",
                kind="postgres",
                strategy="shared_namespaced",
                namespace_template="agent_{ticket_id}",
            ),
        ],
        connection_string_templates={"main-db": "postgresql://x/{namespace}"},
    )
    # Must not raise
    await cleanup_agent_namespace(
        m, agent_id="dev", ticket_id="t-1", success=True, registry=reg
    )


# ---------------------------------------------------------------------------
# Block 2 — OperatorSuppliedProvisioner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operator_supplied_yields_template_string():
    """``operator_supplied`` services pass through the template string."""
    from jig.dev_env.provisioning import OperatorSuppliedProvisioner

    provisioner = OperatorSuppliedProvisioner()
    service = ManifestService(
        id="vendor-queue",
        kind="nats",
        strategy="operator_supplied",
        namespace_template="x",
    )
    url = await provisioner.provision(
        service,
        connection_string_template="nats://operator.example.com:4222",
        agent_id="dev",
        ticket_id="t-1",
        epic_id=None,
    )
    assert url == "nats://operator.example.com:4222"


@pytest.mark.asyncio
async def test_operator_supplied_substitutes_id_placeholders():
    """Operators can still parameterize on agent/ticket/epic for shared
    backing stores (e.g. a hosted Postgres with per-agent search_path)."""
    from jig.dev_env.provisioning import OperatorSuppliedProvisioner

    provisioner = OperatorSuppliedProvisioner()
    service = ManifestService(
        id="hosted-db",
        kind="postgres",
        strategy="operator_supplied",
        namespace_template="x",
    )
    url = await provisioner.provision(
        service,
        connection_string_template=("postgres://hosted/db?app={agent_id}_{ticket_id}"),
        agent_id="Dev-1",
        ticket_id="T-001",
        epic_id=None,
    )
    # Sanitization runs through ``_sanitize`` (lowercase + alnum).
    assert url == "postgres://hosted/db?app=dev_1_t_001"


@pytest.mark.asyncio
async def test_operator_supplied_missing_template_raises():
    """A missing ``connection_string_template`` is a manifest bug, not
    silent skip — operators need the loud failure to know they need to
    author one (or pick a different strategy)."""
    from jig.dev_env.provisioning import OperatorSuppliedProvisioner

    provisioner = OperatorSuppliedProvisioner()
    service = ManifestService(
        id="vendor-queue",
        kind="nats",
        strategy="operator_supplied",
        namespace_template="x",
    )
    with pytest.raises(KeyError, match="vendor-queue"):
        await provisioner.provision(
            service,
            connection_string_template="",
            agent_id="dev",
            ticket_id="t-1",
            epic_id=None,
        )


@pytest.mark.asyncio
async def test_operator_supplied_cleanup_is_noop():
    """Operator owns the lifecycle — cleanup must never act."""
    from jig.dev_env.provisioning import OperatorSuppliedProvisioner

    provisioner = OperatorSuppliedProvisioner()
    service = ManifestService(
        id="vendor-queue",
        kind="nats",
        strategy="operator_supplied",
        namespace_template="x",
    )
    # Both success + failure paths return None and don't raise.
    await provisioner.cleanup(
        service,
        agent_id="d",
        ticket_id="t",
        success=True,
        epic_id=None,
    )
    await provisioner.cleanup(
        service,
        agent_id="d",
        ticket_id="t",
        success=False,
        epic_id=None,
    )


@pytest.mark.asyncio
async def test_dispatch_routes_operator_supplied_services():
    """End-to-end: operator-supplied services in a manifest yield URLs."""
    m = DevManifest(
        services=[
            ManifestService(
                id="vendor-queue",
                kind="nats",
                strategy="operator_supplied",
                namespace_template="x",
            ),
        ],
        connection_string_templates={
            "vendor-queue": "nats://operator.example.com:4222",
        },
    )
    out = await provision_agent_namespace(
        m,
        agent_id="dev",
        ticket_id="t-1",
    )
    assert out == {"vendor-queue": "nats://operator.example.com:4222"}

    # Cleanup must be a no-op — the operator owns the service lifecycle.
    await cleanup_agent_namespace(
        m,
        agent_id="dev",
        ticket_id="t-1",
        success=True,
    )
