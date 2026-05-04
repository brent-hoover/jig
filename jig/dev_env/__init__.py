"""Track E MVP — service isolation primitives.

Public surface:

- ``derive_manifest`` — pure function ``Architecture -> DevManifest``
- ``provision_agent_namespace`` / ``cleanup_agent_namespace`` — orchestrator
  hook entry points
- ``OrphanTracker`` — operator-facing orphan-namespace inventory
"""
from jig.dev_env.ephemeral import (
    EphemeralProvisioner,
    PostgresDbEphemeralProvisioner,
    SqliteEphemeralProvisioner,
    ephemeral_archive_dir,
    ephemeral_root,
)
from jig.dev_env.manifest import derive_manifest
from jig.dev_env.orphans import (
    OrphanLogEntry,
    OrphanedNamespace,
    OrphanTracker,
    append_orphan_log,
    drop_orphan,
    list_orphans,
    orphan_log_path,
)
from jig.dev_env.provisioning import (
    NamespaceProvisioner,
    NatsSubjectPrefixProvisioner,
    PostgresSchemaProvisioner,
    ProvisioningRegistry,
    RedisKeyPrefixProvisioner,
    S3BucketPrefixProvisioner,
    cleanup_agent_namespace,
    provision_agent_namespace,
)

__all__ = [
    "EphemeralProvisioner",
    "NamespaceProvisioner",
    "NatsSubjectPrefixProvisioner",
    "OrphanLogEntry",
    "OrphanTracker",
    "OrphanedNamespace",
    "PostgresDbEphemeralProvisioner",
    "PostgresSchemaProvisioner",
    "ProvisioningRegistry",
    "RedisKeyPrefixProvisioner",
    "S3BucketPrefixProvisioner",
    "SqliteEphemeralProvisioner",
    "append_orphan_log",
    "cleanup_agent_namespace",
    "derive_manifest",
    "drop_orphan",
    "ephemeral_archive_dir",
    "ephemeral_root",
    "list_orphans",
    "orphan_log_path",
    "provision_agent_namespace",
]
