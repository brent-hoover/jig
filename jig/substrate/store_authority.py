"""StoreAuthority — the unified ``project://`` read/write facade (Epic 2, task 2).

The 5 authorities (``spec``/``arch``/``design``/``plan``/``store``) are today
resolved by per-authority functions behind :func:`jig.uri.resolve_project_uri`,
and written by scattered store wrappers. ``StoreAuthority`` is the single seam
those collapse behind.

Bones scope: ``read`` delegates to the existing resolver — so it routes
correctly today and honestly surfaces ``UnimplementedAuthorityError`` for the
authorities not yet wired (arch/design/plan/store). ``write`` is the declared
seam; MVP routes the existing JSONL stores through it (no new persistence).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import parse_project_uri
from jig.uri.resolver import ResolvedUri, resolve_project_uri

if TYPE_CHECKING:
    from jig.uri.cache import UriResolverCache


class StoreAuthority:
    """Unified read/write interface over the project's durable artifacts."""

    AUTHORITIES: tuple[str, ...] = ("spec", "arch", "design", "plan", "store")

    def __init__(
        self, project_root: Path, *, cache: "UriResolverCache | None" = None
    ) -> None:
        self._root = Path(project_root)
        self._cache = cache

    def authority_of(self, uri: str) -> str:
        """Which of the 5 authorities a ``project://`` URI routes to."""
        return parse_project_uri(uri).authority

    def read(self, uri: str) -> ResolvedUri:
        """Resolve a ``project://`` URI to its artifact (or fragment thereof)."""
        return resolve_project_uri(uri, self._root, cache=self._cache)

    async def write(self, uri: str, doc: dict) -> str:
        """Write ``doc`` to the artifact addressed by ``uri``.

        Bones seam: MVP routes the existing JSONL stores through here. The URI
        is parsed (so malformed URIs still fail fast) before the seam raises.
        """
        authority = self.authority_of(uri)
        raise UnimplementedAuthorityError(
            f"StoreAuthority.write for authority {authority!r} is wired in Epic 2 MVP"
        )
