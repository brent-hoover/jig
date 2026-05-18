"""Manifest derivation (Track E MVP deliverable 2).

Pure function ``derive_manifest(architecture) -> DevManifest`` that walks
``architecture.data_stores`` and emits one ``ManifestService`` entry per
store with a non-None ``dev_provisioning`` block. Stores without a
provisioning block are skipped (operator-shared / no isolation needed).

Per ``docs/v2.0/dev-environment/design.md``: the manifest is a generated
artifact, never hand-edited. ``dev_derive_manifest`` MCP tool re-runs
this and rewrites the on-disk file every time the architecture changes.
"""

from __future__ import annotations

from jig.schemas.arch import Architecture
from jig.schemas.dev_env import (
    DevManifest,
    ManifestService,
    derive_default_connection_string_template,
)

__all__ = ["derive_manifest"]


def derive_manifest(architecture: Architecture) -> DevManifest:
    """Project ``architecture.data_stores`` into a ``DevManifest``.

    Skips data stores without a ``dev_provisioning`` block — those are
    operator-shared / no-isolation-needed and the orchestrator's
    provisioning step does nothing for them. For stores with an empty
    ``connection_string_template``, fills in a per-kind default
    (Postgres on localhost:5432 etc.) so the agent always gets a
    usable URL in its env. Operator-shipped overrides go in the
    architecture YAML, not here.
    """
    services: list[ManifestService] = []
    templates: dict[str, str] = {}
    for store in architecture.data_stores:
        prov = store.dev_provisioning
        if prov is None:
            continue
        services.append(
            ManifestService(
                id=store.id,
                kind=store.kind,
                strategy=prov.strategy,
                namespace_template=prov.namespace_template,
                cleanup_on_success=prov.cleanup_on_success,
                cleanup_on_failure=prov.cleanup_on_failure,
            )
        )
        template = prov.connection_string_template or (
            derive_default_connection_string_template(store.kind)
        )
        templates[store.id] = template
    return DevManifest(
        services=services,
        connection_string_templates=templates,
    )
