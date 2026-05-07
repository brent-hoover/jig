"""Tests for dev-environment manifest derivation (Track E MVP)."""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.dev_env import derive_manifest
from jig.dev_env_mcp import handle_dev_derive_manifest
from jig.intent import Intent
from jig.schemas.arch import (
    Architecture,
    DataStore,
    DevProvisioning,
    Module,
)
from jig.schemas.dev_env import (
    DevManifest,
    ManifestService,
    derive_default_connection_string_template,
)
from pydantic import ValidationError
from jig.spec_loader import (
    dev_manifest_path,
    load_dev_manifest,
    save_architecture,
    save_dev_manifest,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind, snippet",
    [
        ("postgres", "postgresql://"),
        ("nats", "nats://"),
        ("redis", "redis://"),
        ("s3", "s3://"),
        ("sqlite", "sqlite:"),
    ],
)
def test_default_connection_string_templates_exist_for_supported_kinds(
    kind: str, snippet: str
) -> None:
    template = derive_default_connection_string_template(kind)
    assert snippet in template
    assert "{namespace}" in template


def test_default_connection_string_template_unknown_kind_returns_empty():
    assert derive_default_connection_string_template("nonsense") == ""


# ---------------------------------------------------------------------------
# Pure derivation
# ---------------------------------------------------------------------------


def test_derive_manifest_skips_stores_without_dev_provisioning():
    arch = Architecture(
        data_stores=[
            DataStore(id="legacy", kind="mysql"),  # no dev_provisioning
        ],
    )
    m = derive_manifest(arch)
    assert m.services == []
    assert m.connection_string_templates == {}


def test_derive_manifest_emits_one_service_per_provisioned_store():
    arch = Architecture(
        data_stores=[
            DataStore(
                id="main-db",
                kind="postgres",
                dev_provisioning=DevProvisioning(strategy="shared_namespaced"),
            ),
            DataStore(
                id="event-bus",
                kind="nats",
                dev_provisioning=DevProvisioning(
                    strategy="shared_namespaced",
                    namespace_template="agent.{ticket_id}",
                ),
            ),
            DataStore(id="legacy", kind="mysql"),  # skipped
        ],
    )
    m = derive_manifest(arch)
    ids = [s.id for s in m.services]
    assert ids == ["main-db", "event-bus"]
    assert m.services[1].namespace_template == "agent.{ticket_id}"
    # Default templates fill in for empty connection_string_template
    assert "postgresql://" in m.connection_string_templates["main-db"]
    assert "nats://" in m.connection_string_templates["event-bus"]
    assert "legacy" not in m.connection_string_templates


def test_derive_manifest_preserves_explicit_connection_string_template():
    arch = Architecture(
        data_stores=[
            DataStore(
                id="main-db",
                kind="postgres",
                dev_provisioning=DevProvisioning(
                    strategy="shared_namespaced",
                    connection_string_template=(
                        "postgresql://app:secret@db.example.com/foo?search_path={namespace}"
                    ),
                ),
            ),
        ],
    )
    m = derive_manifest(arch)
    assert (
        m.connection_string_templates["main-db"]
        == "postgresql://app:secret@db.example.com/foo?search_path={namespace}"
    )


def test_derive_manifest_strategy_stubs_round_trip():
    """per_agent_ephemeral / operator_supplied parse but stay as stubs."""
    arch = Architecture(
        data_stores=[
            DataStore(
                id="ephemeral-db",
                kind="sqlite",
                dev_provisioning=DevProvisioning(strategy="per_agent_ephemeral"),
            ),
            DataStore(
                id="op-pg",
                kind="postgres",
                dev_provisioning=DevProvisioning(strategy="operator_supplied"),
            ),
        ],
    )
    m = derive_manifest(arch)
    by_id = {s.id: s for s in m.services}
    assert by_id["ephemeral-db"].strategy == "per_agent_ephemeral"
    assert by_id["op-pg"].strategy == "operator_supplied"


# ---------------------------------------------------------------------------
# Disk round-trip via save/load helpers
# ---------------------------------------------------------------------------


def test_save_and_load_dev_manifest_round_trip(tmp_path: Path):
    arch = Architecture(
        data_stores=[
            DataStore(
                id="main-db",
                kind="postgres",
                dev_provisioning=DevProvisioning(strategy="shared_namespaced"),
            ),
        ],
    )
    m = derive_manifest(arch)
    save_dev_manifest(tmp_path, m)
    assert dev_manifest_path(tmp_path).is_file()
    loaded = load_dev_manifest(tmp_path)
    assert isinstance(loaded, DevManifest)
    assert [s.id for s in loaded.services] == ["main-db"]


def test_load_dev_manifest_missing_file_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_dev_manifest(tmp_path)


# ---------------------------------------------------------------------------
# MCP handler — happy + missing-architecture paths
# ---------------------------------------------------------------------------


def _intent(p: str = "P", s: str = "S") -> Intent:
    return Intent(problem=p, simplest_solution=s)


@pytest.mark.asyncio
async def test_handle_dev_derive_manifest_writes_file(tmp_path: Path):
    arch = Architecture(
        data_stores=[
            DataStore(
                id="main-db",
                kind="postgres",
                dev_provisioning=DevProvisioning(strategy="shared_namespaced"),
            ),
        ],
        modules=[
            Module(
                id="catalog-ingest",
                title="Catalog Ingest",
                summary="x",
                intent=_intent(),
            )
        ],
    )
    save_architecture(tmp_path, arch)

    written = await handle_dev_derive_manifest(project_path=tmp_path)
    assert Path(written).is_file()
    loaded = load_dev_manifest(tmp_path)
    assert [s.id for s in loaded.services] == ["main-db"]


@pytest.mark.asyncio
async def test_handle_dev_derive_manifest_missing_architecture_raises(
    tmp_path: Path,
):
    with pytest.raises(FileNotFoundError):
        await handle_dev_derive_manifest(project_path=tmp_path)


# ---------------------------------------------------------------------------
# DevManifest schema-level invariants (Block A.2)
# ---------------------------------------------------------------------------


def _service(svc_id: str = "main-db") -> ManifestService:
    return ManifestService(
        id=svc_id,
        kind="postgres",
        strategy="shared_namespaced",
        namespace_template="agent_{ticket_id}",
    )


def test_dev_manifest_rejects_duplicate_service_ids():
    with pytest.raises(ValidationError, match="duplicate service id"):
        DevManifest(
            services=[_service("svc"), _service("svc")],
            connection_string_templates={
                "svc": "postgresql://localhost:5432/jigdev?x={namespace}",
            },
        )


def test_dev_manifest_rejects_service_without_template():
    """Reference-integrity gate: every service id must appear in
    ``connection_string_templates``. The provisioner reads the
    template at provision time; a missing entry is a runtime
    KeyError on the agent's first I/O.
    """
    with pytest.raises(ValidationError, match="missing from connection_string_templates"):
        DevManifest(
            services=[_service("svc-a"), _service("svc-b")],
            connection_string_templates={
                "svc-a": "postgresql://localhost:5432/jigdev?x={namespace}",
            },
        )


def test_dev_manifest_accepts_unique_services_with_full_templates():
    m = DevManifest(
        services=[_service("svc-a"), _service("svc-b")],
        connection_string_templates={
            "svc-a": "postgresql://localhost:5432/a?x={namespace}",
            "svc-b": "postgresql://localhost:5432/b?x={namespace}",
        },
    )
    assert {s.id for s in m.services} == {"svc-a", "svc-b"}


def test_dev_manifest_empty_round_trip():
    """No services → no template requirements; both invariants
    vacuously hold."""
    m = DevManifest()
    assert m.services == []
    assert m.connection_string_templates == {}
