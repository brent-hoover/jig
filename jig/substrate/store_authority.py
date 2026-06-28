"""StoreAuthority — the unified ``project://`` read/write facade (Epic 2).

The 5 authorities (``spec``/``arch``/``design``/``plan``/``store``) are today
resolved by per-authority functions behind :func:`jig.uri.resolve_project_uri`,
and written by scattered store wrappers. ``StoreAuthority`` is the single seam
those collapse behind.

``read`` delegates to the existing resolver. ``write`` is the **security gate**
(MVP task 4, the reviewable design that gates production routing): every write
parses + validates the URI (rejecting malformed/traversal URIs and authorities
outside the closed set — enforced by the parser's strict segment rule) and
enforces **authorization-by-authority**. A ``StoreAuthority`` may be scoped to a
set of *writable* authorities; a write outside that set is rejected with a typed
``WriteNotAuthorizedError``, never silently coerced. Persistence is wired in a
follow-on PR — a write that passes the gate raises ``UnimplementedAuthorityError``
for now.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import parse_project_uri
from jig.uri.resolver import ResolvedUri, resolve_project_uri

if TYPE_CHECKING:
    from jig.uri.cache import UriResolverCache


class WriteNotAuthorizedError(PermissionError):
    """A write targets a valid authority this StoreAuthority is not scoped to
    write. Distinct from a malformed/unknown URI (``ProjectUriError``) — the URI
    is well-formed; the instance simply isn't authorized to write there."""


class StoreAuthority:
    """Unified read/write interface over the project's durable artifacts."""

    AUTHORITIES: tuple[str, ...] = ("spec", "arch", "design", "plan", "store")

    def __init__(
        self,
        project_root: Path,
        *,
        cache: "UriResolverCache | None" = None,
        writable: Iterable[str] | None = None,
    ) -> None:
        self._root = Path(project_root)
        self._cache = cache
        # The authorities this instance may write. Default: all five. A scoped
        # instance (e.g. Discovery -> {"spec"}) rejects writes elsewhere.
        if writable is None:
            self._writable: frozenset[str] = frozenset(self.AUTHORITIES)
        else:
            self._writable = frozenset(writable)
            invalid = self._writable - set(self.AUTHORITIES)
            if invalid:
                raise ValueError(
                    f"{sorted(invalid)} is not a valid authority; "
                    f"must be a subset of {self.AUTHORITIES}"
                )

    @property
    def writable(self) -> frozenset[str]:
        """The authorities this instance is authorized to write."""
        return self._writable

    def authority_of(self, uri: str) -> str:
        """Which of the 5 authorities a ``project://`` URI routes to."""
        return parse_project_uri(uri).authority

    def read(self, uri: str) -> ResolvedUri:
        """Resolve a ``project://`` URI to its artifact (or fragment thereof)."""
        return resolve_project_uri(uri, self._root, cache=self._cache)

    async def write(self, uri: str, doc: dict) -> str:
        """Write ``doc`` to the artifact addressed by ``uri`` — through the
        security gate.

        Order: (1) parse/validate the URI — malformed, path-traversal, and
        out-of-set authorities all fail here via the parser's strict rules;
        (2) authorize the authority against this instance's writable set;
        (3) persist (not yet wired — raises ``UnimplementedAuthorityError``).
        """
        authority = parse_project_uri(uri).authority  # (1) validate
        if authority not in self._writable:  # (2) authorize
            raise WriteNotAuthorizedError(
                f"not authorized to write authority {authority!r}; "
                f"this StoreAuthority may write {sorted(self._writable)}"
            )
        raise UnimplementedAuthorityError(  # (3) persistence: follow-on PR
            f"StoreAuthority.write persistence for authority {authority!r} "
            "is not yet wired"
        )
