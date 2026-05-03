"""Resolver for ``project://design/...`` URIs (VD output).

Stub for v2 bones — raises ``UnimplementedAuthorityError``. Wired up in
Track D as ``frontend.yaml``, ``system/``, and ``wireframes/`` land.
See ``docs/uri-scheme/design.md`` §"`project://design/...`".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import ProjectUri


def resolve_design_uri(uri: ProjectUri, project_root: Path) -> dict[str, Any]:
    raise UnimplementedAuthorityError(
        "design authority not yet implemented (Track D); "
        f"got {uri!r}"
    )
