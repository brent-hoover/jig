"""Per-agent namespace provisioning hooks (Track E MVP deliverable 3).

Stub module — the concrete provisioner classes land in commit 3.
"""
from __future__ import annotations


class NamespaceProvisioner:
    """Placeholder; concrete implementation lands in commit 3."""


class PostgresSchemaProvisioner(NamespaceProvisioner):
    """Placeholder; concrete implementation lands in commit 3."""


class NatsSubjectPrefixProvisioner(NamespaceProvisioner):
    """Placeholder; concrete implementation lands in commit 3."""


class S3BucketPrefixProvisioner(NamespaceProvisioner):
    """Placeholder; concrete implementation lands in commit 3."""


class RedisKeyPrefixProvisioner(NamespaceProvisioner):
    """Placeholder; concrete implementation lands in commit 3."""


class ProvisioningRegistry:
    """Placeholder; concrete implementation lands in commit 3."""


async def provision_agent_namespace(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise NotImplementedError("provisioning lands in commit 3")


async def cleanup_agent_namespace(*args, **kwargs):  # type: ignore[no-untyped-def]
    raise NotImplementedError("provisioning lands in commit 3")
