"""Resolver for ``project://arch/...`` URIs (SA output).

Stub for v2 bones — raises ``UnimplementedAuthorityError``. Wired up in
Track C as ``architecture.yaml`` and ``modules/<m>/contracts.yaml`` schemas
land. See ``docs/v2.0/uri-scheme/design.md`` §"`project://arch/...`".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import ProjectUri


def resolve_arch_uri(uri: ProjectUri, project_root: Path) -> dict[str, Any]:
    raise UnimplementedAuthorityError(
        "arch authority not yet implemented (Track C); "
        f"got {uri!r}"
    )
