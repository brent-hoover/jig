"""Track E MVP — service isolation primitives.

Public surface:

- ``derive_manifest`` — pure function ``Architecture -> DevManifest``
- ``provision_agent_namespace`` / ``cleanup_agent_namespace`` — orchestrator
  hook entry points
- ``OrphanTracker`` — operator-facing orphan-namespace inventory
"""

from jig.dev_env.ephemeral import (
    EphemeralInstance,
    EphemeralProvisioner,
    InspectResult,
    PostgresDbEphemeralProvisioner,
    SqliteEphemeralProvisioner,
    drop_ephemeral_instance,
    ephemeral_archive_dir,
    ephemeral_root,
    inspect_ephemeral_instance,
    list_ephemeral_instances,
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
from jig.dev_env.sweeper import (
    OrphanSweeper,
    SweepBucket,
    SweepReport,
    sweeper_log_path,
)

__all__ = [
    "EphemeralInstance",
    "EphemeralProvisioner",
    "InspectResult",
    "NamespaceProvisioner",
    "NatsSubjectPrefixProvisioner",
    "OrphanLogEntry",
    "OrphanSweeper",
    "OrphanTracker",
    "OrphanedNamespace",
    "PostgresDbEphemeralProvisioner",
    "PostgresSchemaProvisioner",
    "ProvisioningRegistry",
    "RedisKeyPrefixProvisioner",
    "S3BucketPrefixProvisioner",
    "SqliteEphemeralProvisioner",
    "SweepBucket",
    "SweepReport",
    "append_orphan_log",
    "cleanup_agent_namespace",
    "derive_manifest",
    "drop_ephemeral_instance",
    "drop_orphan",
    "ephemeral_archive_dir",
    "ephemeral_root",
    "inspect_ephemeral_instance",
    "list_ephemeral_instances",
    "list_orphans",
    "orphan_log_path",
    "provision_agent_namespace",
    "sweeper_log_path",
]
