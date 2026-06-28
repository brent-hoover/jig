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
``WriteNotAuthorizedError``, never silently coerced.

Past the gate, ``store`` writes persist through the typed ``TicketStore`` (PR B2)
— never a raw dict append — so the store's invariants hold: schema validation,
the operator-gated ``PROPOSED -> OPEN`` transition rule, the cross-process create
lock + ``jig-N`` key counter, and the create/status-change callbacks. The other
four authorities (spec/arch/design/plan) raise ``UnimplementedAuthorityError``
until their owning tracks wire persistence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jig.uri.errors import ProjectUriError, UnimplementedAuthorityError
from jig.uri.parser import ProjectUri, parse_project_uri
from jig.uri.resolver import ResolvedUri, resolve_project_uri

# Same grammar the parser applies to path segments; the parser does NOT apply it
# to fragment components, so the write gate validates those itself.
_SAFE_SEGMENT = re.compile(r"^[a-z0-9_-]+$")

if TYPE_CHECKING:
    from jig.store.tickets import TicketStore
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
        tickets: "TicketStore | None" = None,
    ) -> None:
        self._root = Path(project_root)
        self._cache = cache
        # The typed ticket store backing ``store`` writes. When injected (the
        # orchestrator passes its already-loaded, callback-wired instance) we
        # share it — one loaded store, so create-lock/seq state and the bus
        # callbacks are consistent. When ``None`` we lazily build + load one on
        # the canonical path; that owned instance has no callbacks (nothing to
        # announce to) but still enforces every schema/transition/lock invariant.
        self._ticket_store = tickets
        self._owns_ticket_store = tickets is None
        # The authorities this instance may write. Default: all five. A scoped
        # instance (e.g. Discovery -> {"spec"}) rejects writes elsewhere; the
        # empty set is a valid **read-only** authority (every write rejected,
        # reads still work).
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

    async def write(self, uri: str, doc: dict[str, Any]) -> str:
        """Write ``doc`` to the artifact addressed by ``uri`` — through the
        security gate.

        Order: (1) parse/validate the URI — malformed, path-traversal, and
        out-of-set authorities all fail here via the parser's strict rules, plus
        a fragment-safety check the parser doesn't do; (2) authorize the
        authority against this instance's writable set; (3) persist (not yet
        wired — raises ``UnimplementedAuthorityError``).
        """
        parsed = parse_project_uri(uri)  # (1) validate path + authority
        if parsed.fragment is not None:  # ...and the fragment (parser skips it)
            for component in parsed.fragment.split("/"):
                if not _SAFE_SEGMENT.match(component):
                    raise ProjectUriError(
                        f"unsafe fragment component {component!r} in {uri!r}"
                    )
        authority = parsed.authority
        if authority not in self._writable:  # (2) authorize
            raise WriteNotAuthorizedError(
                f"not authorized to write authority {authority!r}; "
                f"this StoreAuthority may write {sorted(self._writable)}"
            )
        # (3) persist. ``store`` routes through the typed TicketStore (preserving
        # schema/transition/lock/callbacks); the other authorities are follow-on.
        if authority == "store":
            return await self._write_store(parsed, doc)
        raise UnimplementedAuthorityError(
            f"StoreAuthority.write persistence for authority {authority!r} "
            "is not yet wired"
        )

    async def _tickets(self) -> "TicketStore":
        """The TicketStore backing ``store`` writes. Injected instances are used
        as-is (caller owns their load/callback lifecycle); an owned instance is
        built + loaded once on the canonical path and cached."""
        from jig.store.tickets import TicketStore

        if self._ticket_store is None:
            store = TicketStore(self._root / ".jig" / "store" / "tickets.jsonl")
            await store.load()
            self._ticket_store = store
        return self._ticket_store

    async def _write_store(self, parsed: ProjectUri, doc: dict[str, Any]) -> str:
        """Persist a ``project://store/tickets/<id>`` write through TicketStore.

        Create when the id is new, update when it exists. The URI id is
        authoritative — a conflicting id in the body is rejected, not coerced.
        Only the ``tickets`` collection is wired; other store collections raise.
        """
        collection = parsed.path[0] if parsed.path else None
        if collection != "tickets":
            raise UnimplementedAuthorityError(
                f"store write for collection {collection!r} is not yet wired; "
                "only project://store/tickets/<id> is supported"
            )
        if parsed.revision is not None:
            raise ProjectUriError(f"store writes cannot pin @revision; got {parsed!r}")
        if parsed.fragment is not None:
            raise ProjectUriError(
                f"store writes cannot target a fragment; got {parsed!r}"
            )
        if len(parsed.path) != 2:
            raise ProjectUriError(
                "store write must target a single ticket "
                f"(project://store/tickets/<id>); got {parsed!r}"
            )

        ticket_id = parsed.path[1]
        store = await self._tickets()
        if self._owns_ticket_store:
            # An owned store's in-memory snapshot can go stale relative to
            # another process between writes. Reload so the create-vs-update
            # decision reflects current disk state — otherwise a write to a
            # ticket another process already created would wrongly take the
            # create branch (spurious "already exists", or a partial body
            # failing full-ticket validation). An *injected* store is the
            # caller's live, authoritative instance; we trust it's current.
            await store.load()
        existing = await store.get(ticket_id)
        if existing is None:
            from jig.ticket import Ticket

            # current_key="" — no ticket yet, so the body must not preset a key.
            body = self._prepare_body(ticket_id, doc, current_key="")
            # enforce_unique_id: the explicit URI id is checked against disk
            # under the store's cross-process lock (the in-memory miss above can
            # be stale relative to a concurrent writer).
            await store.create(
                Ticket.model_validate({**body, "_id": ticket_id}),
                enforce_unique_id=True,
            )
        else:
            body = self._prepare_body(ticket_id, doc, current_key=existing.key)
            await store.update(ticket_id, **body)
        return ticket_id

    @staticmethod
    def _prepare_body(
        ticket_id: str, doc: dict[str, Any], *, current_key: str
    ) -> dict[str, Any]:
        """Strip the server-owned identity fields (``id``/``_id``, ``key``) from
        the write body and reject any attempt to set them to a new value.

        The id is the address, not a mutable field. The ``jig-N`` ``key`` is
        assigned by ``TicketStore`` and is immutable. A body may *echo* the
        correct value (natural for a read-modify-write round-trip), but a
        *different* value is a caller bug surfaced loudly, never silently won."""
        for id_key in ("id", "_id"):
            if id_key in doc and doc[id_key] != ticket_id:
                raise ProjectUriError(
                    f"ticket body {id_key}={doc[id_key]!r} conflicts with "
                    f"URI id {ticket_id!r}"
                )
        if "key" in doc and doc["key"] not in ("", current_key):
            raise ProjectUriError(
                f"ticket body key={doc['key']!r} cannot set or change the "
                f"server-assigned jig-N key (current {current_key!r})"
            )
        return {k: v for k, v in doc.items() if k not in ("id", "_id", "key")}
