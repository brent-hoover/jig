"""Orchestrator integration for the dev-env provisioning hooks (Track E MVP).

Wraps the manifest-load + provision + cleanup paths so
``Orchestrator._run_agent_with_analytics`` can pre-provision and
post-cleanup the per-agent namespaces around ``run_agent``. Best-effort:
any failure short-circuits provisioning to "no env vars added" and
logs the cause; the agent still runs (matches the design's "fail-fast
at HEALTH-CHECK" but for MVP scope without the reachability probe).

Per ``docs/v2.0/dev-environment/design.md`` §"Connection injection — env
vars": each provisioned service contributes one env var named
``JIG_DEV_<SERVICE_ID_UPPER>_URL``. Service IDs are sanitized to
``[A-Z0-9_]`` so the env-var name is always valid.
"""

from __future__ import annotations

import logging
from pathlib import Path

from jig.dev_env.fixtures import (
    FIXTURE_MODE_ENV_VAR,
    FixtureMode,
    fixture_mode_for_ticket,
)
from jig.dev_env.provisioning import (
    ProvisioningRegistry,
    cleanup_agent_namespace,
    provision_agent_namespace,
)
from jig.schemas.dev_env import DevManifest
from jig.spec_loader import load_dev_manifest
from jig.ticket import Ticket

__all__ = [
    "ENV_VAR_PREFIX",
    "build_env_var_name",
    "build_fixture_env",
    "load_manifest_or_none",
    "provision_for_agent",
    "cleanup_for_agent",
    "DevProvisioningError",
]


class DevProvisioningError(RuntimeError):
    """Raised when ``provision_for_agent`` cannot stand up the
    per-agent namespace. SF-1: the orchestrator must surface this as
    a spawn failure for tickets that declare dev services rather than
    silently dropping the env map and letting the agent run against
    default/local services."""


_logger = logging.getLogger(__name__)

ENV_VAR_PREFIX = "JIG_DEV_"


def build_env_var_name(service_id: str) -> str:
    """Map ``service_id`` to ``JIG_DEV_<SERVICE_ID>_URL``.

    Sanitizes to ``[A-Z0-9_]`` so the resulting name is always a valid
    env var across shells (POSIX requires ``[A-Z_][A-Z0-9_]*``).
    """
    safe = "".join(ch.upper() if ch.isalnum() else "_" for ch in service_id)
    return f"{ENV_VAR_PREFIX}{safe}_URL"


def load_manifest_or_none(project_path: Path) -> DevManifest | None:
    """Load the dev manifest if present; return None when absent.

    Absence is the common case during bones runs and for projects that
    haven't run SA yet — it must not crash the orchestrator's spawn
    path.
    """
    try:
        return load_dev_manifest(project_path)
    except FileNotFoundError:
        return None


async def provision_for_agent(
    project_path: Path,
    *,
    agent_id: str,
    ticket_id: str,
    epic_id: str | None = None,
    registry: ProvisioningRegistry | None = None,
) -> dict[str, str]:
    """Provision per-agent namespaces and return the env-var map.

    Returns ``{JIG_DEV_<SERVICE>_URL: connection_string}``. Empty when
    no manifest exists or no services produce a URL.

    Raises ``DevProvisioningError`` if the manifest declares services
    but provisioning fails. The orchestrator catches this and marks
    the spawn failed rather than letting the agent run against default
    services with a silently empty env map (SF-1).
    """
    manifest = load_manifest_or_none(project_path)
    if manifest is None or not manifest.services:
        return {}
    try:
        url_map = await provision_agent_namespace(
            manifest,
            agent_id=agent_id,
            ticket_id=ticket_id,
            epic_id=epic_id,
            registry=registry,
            project_root=project_path,
        )
    except Exception as exc:
        _logger.warning(
            "dev-env provisioning failed for agent=%s ticket=%s",
            agent_id,
            ticket_id,
            exc_info=True,
        )
        raise DevProvisioningError(
            f"dev-env provisioning failed for ticket {ticket_id!r}: {exc}"
        ) from exc
    return {build_env_var_name(sid): url for sid, url in url_map.items()}


async def cleanup_for_agent(
    project_path: Path,
    *,
    agent_id: str,
    ticket_id: str,
    success: bool,
    epic_id: str | None = None,
    registry: ProvisioningRegistry | None = None,
) -> None:
    """Best-effort cleanup; never propagates failures.

    Mirrors the orchestrator's existing pattern around analytics drain
    on shutdown — a cleanup failure must not cascade into the spawn
    path's exception handling.
    """
    manifest = load_manifest_or_none(project_path)
    if manifest is None or not manifest.services:
        return
    try:
        await cleanup_agent_namespace(
            manifest,
            agent_id=agent_id,
            ticket_id=ticket_id,
            success=success,
            epic_id=epic_id,
            registry=registry,
            project_root=project_path,
        )
    except Exception:
        _logger.warning(
            "dev-env cleanup failed for agent=%s ticket=%s success=%s",
            agent_id,
            ticket_id,
            success,
            exc_info=True,
        )


def build_fixture_env(ticket: Ticket, *, override: str | None = None) -> dict[str, str]:
    """Return ``{JIG_FIXTURE_MODE: <mode>}`` for the agent's spawn env.

    Per ``docs/v2.0/dev-environment/design.md`` §"External-API recorded
    fixtures": SPIKE work_type → ``record_new`` (the spike's job is to
    grow the fixture corpus); everything else → ``replay_only``.
    Per-spawn ``override`` (e.g. an ``arch_propose_spike`` carrying
    ``fixture_mode_override="bypass"``) wins.

    Returns a single-entry dict so the caller can ``env.update(...)``
    it onto the bwrap-injected env map alongside the dev-service URLs.
    """
    mode: FixtureMode = fixture_mode_for_ticket(ticket, override=override)
    return {FIXTURE_MODE_ENV_VAR: mode.value}
