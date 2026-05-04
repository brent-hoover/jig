"""Track E MVP — service isolation primitives.

Public surface:

- ``derive_manifest`` — pure function ``Architecture -> DevManifest``
- ``provision_agent_namespace`` / ``cleanup_agent_namespace`` — orchestrator
  hook entry points
- ``OrphanTracker`` — operator-facing orphan-namespace inventory
"""
from jig.dev_env.manifest import derive_manifest
from jig.dev_env.orphans import (
    OrphanedNamespace,
    OrphanTracker,
    drop_orphan,
    list_orphans,
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
    "NamespaceProvisioner",
    "NatsSubjectPrefixProvisioner",
    "OrphanTracker",
    "OrphanedNamespace",
    "PostgresSchemaProvisioner",
    "ProvisioningRegistry",
    "RedisKeyPrefixProvisioner",
    "S3BucketPrefixProvisioner",
    "cleanup_agent_namespace",
    "derive_manifest",
    "drop_orphan",
    "list_orphans",
    "provision_agent_namespace",
]
