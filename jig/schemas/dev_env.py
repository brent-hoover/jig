"""Dev-environment manifest schema (Track E MVP deliverable 2).

The manifest is the operational view derived from ``architecture.yaml``'s
``data_stores`` entries — the orchestrator reads it to decide which
namespace to provision per agent on which service. Operators never edit
the manifest directly; ``derive_manifest`` re-projects it from the
architecture every time it changes.

Per ``docs/v2.0/dev-environment/design.md`` §"The dev environment manifest
(derived)":

    .jig/dev/manifest.yaml — generated, not hand-authored
    services:
      - id: main-db
        kind: postgres
        strategy: shared_namespaced
        ...
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jig.schemas._validators import (
    ServiceKind,
    validate_kebab_id,
    validate_tz_aware,
)

__all__ = [
    "DevManifest",
    "ManifestService",
    "derive_default_connection_string_template",
]


class ManifestService(BaseModel):
    """One service entry in the derived manifest.

    Carries the minimum the provisioner + cleanup paths need to do their
    job for an agent: kind (drives the provisioner choice), strategy
    (shared_namespaced is the only one MVP wires; the other two parse
    as stubs), namespace_template (for substitution), cleanup policies
    (success vs failure).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    kind: ServiceKind = Field(
        ..., description="Bounded vocabulary shared with DataStore.kind."
    )
    strategy: Literal[
        "shared_namespaced", "per_agent_ephemeral", "operator_supplied"
    ]
    namespace_template: str = Field(..., min_length=1)
    cleanup_on_success: Literal["drop", "archive", "keep"] = "drop"
    cleanup_on_failure: Literal["drop", "archive", "keep"] = "archive"

    @field_validator("id")
    @classmethod
    def _kebab_id(cls, v: str) -> str:
        return validate_kebab_id(v, "ManifestService.id")


class DevManifest(BaseModel):
    """The full derived manifest.

    Includes a ``connection_string_templates`` map keyed by service id
    so the provisioner can produce per-agent connection strings without
    re-loading the source ``DevProvisioning`` block — the manifest is
    the source of truth at provisioning time.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    services: list[ManifestService] = Field(default_factory=list)
    connection_string_templates: dict[str, str] = Field(default_factory=dict)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @field_validator("generated_at")
    @classmethod
    def _tz_generated_at(cls, v: datetime) -> datetime:
        return validate_tz_aware(v, "DevManifest.generated_at")

    @model_validator(mode="after")
    def _enforce_uniqueness_and_template_coverage(self) -> DevManifest:
        """Two invariants the provisioner depends on:

        1. Service ids unique — the per-service connection-string lookup
           is keyed on ``service.id``; duplicates pick the wrong one.
        2. Every service id has a corresponding entry in
           ``connection_string_templates``. The provisioner reads the
           template at provision time; a missing entry is a runtime
           KeyError on the agent's first I/O.
        """
        seen_ids: set[str] = set()
        dupes: set[str] = set()
        for s in self.services:
            if s.id in seen_ids:
                dupes.add(s.id)
            else:
                seen_ids.add(s.id)
        if dupes:
            raise ValueError(
                f"DevManifest.services: duplicate service id(s) "
                f"{sorted(dupes)!r}. The provisioner keys off service.id."
            )
        missing = [
            s.id for s in self.services
            if s.id not in self.connection_string_templates
        ]
        if missing:
            raise ValueError(
                f"DevManifest: service id(s) "
                f"{sorted(missing)!r} are missing from "
                f"connection_string_templates. The provisioner reads "
                f"the template at provision time; the manifest must "
                f"carry a template for every service it lists."
            )
        return self


# ---------------------------------------------------------------------------
# Defaults — when the architecture's ``connection_string_template`` is
# left empty, the manifest derivation fills in a sensible default per
# kind so the provisioner can still hand the agent a usable URL.
# ---------------------------------------------------------------------------


_DEFAULT_TEMPLATES: dict[str, str] = {
    "postgres": (
        "postgresql://jig:jig@localhost:5432/jigdev"
        "?options=-c%20search_path%3D{namespace}"
    ),
    "nats": "nats://localhost:4222?subject_prefix={namespace}",
    "redis": "redis://localhost:6379/0?key_prefix={namespace}",
    "s3": "s3://localhost:9000/jigdev?bucket_prefix={namespace}",
    "sqlite": "sqlite:///workspace/.dev/{namespace}.db",
}


def derive_default_connection_string_template(kind: str) -> str:
    """Return a baseline URL template for ``kind``; empty when unknown.

    The architecture author overrides this by setting an explicit
    ``connection_string_template`` on the ``DevProvisioning`` block.
    """
    return _DEFAULT_TEMPLATES.get(kind, "")
