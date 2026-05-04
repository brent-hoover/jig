"""Resolver for ``project://plan/...`` URIs (PM output).

Stub for v2 bones — raises ``UnimplementedAuthorityError``. Wired up in
Track F as ``build-plan.yaml`` lands. See ``docs/v2.0/uri-scheme/design.md``
§"`project://plan/...`".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import ProjectUri


def resolve_plan_uri(uri: ProjectUri, project_root: Path) -> dict[str, Any]:
    raise UnimplementedAuthorityError(
        "plan authority not yet implemented (Track F); "
        f"got {uri!r}"
    )
