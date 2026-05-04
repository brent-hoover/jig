"""MCP tool handlers for the dev-environment track (Track E MVP).

Currently exposes one tool:

- ``dev_derive_manifest`` — read ``architecture.yaml``, derive the
  dev-environment manifest, atomically write it to ``.jig/dev/manifest.yaml``.

The handler is a thin wrapper so the MCP factory in ``mcp_server.py``
stays declarative; the actual derivation logic lives in
``jig.dev_env.manifest``.
"""
from __future__ import annotations

from pathlib import Path

from jig.dev_env.manifest import derive_manifest
from jig.spec_loader import (
    dev_manifest_path,
    load_architecture,
    save_dev_manifest,
)


async def handle_dev_derive_manifest(*, project_path: Path) -> str:
    """Derive the dev manifest from architecture.yaml and write it.

    Returns the absolute path of the written manifest as a string so
    the MCP caller can surface it in chat. Raises ``FileNotFoundError``
    if ``architecture.yaml`` is absent — the caller (an SA-tier agent
    or the operator via ``jig dev manifest``) handles the absence by
    running SA first.
    """
    arch = load_architecture(project_path)
    manifest = derive_manifest(arch)
    save_dev_manifest(project_path, manifest)
    return str(dev_manifest_path(project_path))
