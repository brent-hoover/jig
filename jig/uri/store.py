"""Resolver for ``project://store/...`` URIs (runtime JSONL stores).

Stub for v2 bones — raises ``UnimplementedAuthorityError``. Wired up
alongside the JSONL store readers. See ``docs/v2.0/uri-scheme/design.md``
§"`project://store/...`".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import ProjectUri


def resolve_store_uri(uri: ProjectUri, project_root: Path) -> dict[str, Any]:
    raise UnimplementedAuthorityError(
        f"store authority not yet implemented; got {uri!r}"
    )
