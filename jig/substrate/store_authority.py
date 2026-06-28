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

import asyncio
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from jig.uri.errors import ProjectUriError, UnimplementedAuthorityError
from jig.uri.parser import ProjectUri, parse_project_uri
from jig.uri.resolver import ResolvedUri, resolve_project_uri

# Same grammar the parser applies to path segments; the parser does NOT apply it
# to fragment components, so the write gate validates those itself.
_SAFE_SEGMENT = re.compile(r"^[a-z0-9_-]+$")

_StoreT = TypeVar("_StoreT")

# The ``jig-N`` namespace is reserved for the server-assigned ticket ``key``
# (TicketStore.create issues ``jig-1``, ``jig-2``, …). A store-write URI id must
# not borrow it: ``TicketStore.resolve_ref`` checks ids before keys, so a ticket
# whose *id* is ``jig-1`` would shadow whatever ticket later gets *key* ``jig-1``.
# Keep the two namespaces disjoint by rejecting key-shaped ids at the write seam.
_RESERVED_KEY_ID = re.compile(r"^jig-\d+$")

if TYPE_CHECKING:
    from jig.store.check_results import CheckResultsStore
    from jig.store.checkpoints import CheckpointStore
    from jig.store.memory import MemoryStore
    from jig.store.review_comments import ReviewCommentsStore
    from jig.store.threads import ThreadStore
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
        # share it — one store, so create-lock/seq state and the bus callbacks
        # are consistent. When ``None`` we lazily build one on the canonical
        # path; that instance has no callbacks (nothing to announce to) but
        # still enforces every schema/transition/lock invariant. Either way each
        # write reloads it before deciding create vs update (see ``_write_store``).
        self._ticket_store = tickets
        # Composition-root state (ADR-0001): the other domain stores this
        # authority owns + vends as typed ports. Built and loaded by ``load()``;
        # ``None`` until then. URI-only callers never touch these.
        self._threads: "ThreadStore | None" = None
        self._memory: "MemoryStore | None" = None
        self._checkpoints: "CheckpointStore | None" = None
        self._check_results: "CheckResultsStore | None" = None
        self._review_comments: "ReviewCommentsStore | None" = None
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

    # ---- composition root: typed runtime ports (ADR-0001) ---------------

    async def load(self) -> None:
        """Construct + load the domain stores this authority owns and vends.

        Composition-root mode: runtime/in-engine code obtains typed ports
        (:attr:`tickets`, :attr:`threads`, …) from the authority instead of
        constructing its own stores. Call once at startup. URI read/write keep
        working without ``load()`` (the write path builds its ticket store
        on demand); only the typed accessors require it.

        An injected ticket store (the write-path back-compat / test seam) is
        used as-is and assumed already loaded; everything else is built here on
        the canonical ``.jig/store`` paths and loaded together.

        Idempotent: a second call is a no-op — re-running it would orphan the
        already-vended stores (and break the shared-instance invariant for
        everything but tickets). Call once at startup.
        """
        if self._threads is not None:
            return
        from jig.store.check_results import CheckResultsStore
        from jig.store.checkpoints import CheckpointStore
        from jig.store.memory import MemoryStore
        from jig.store.review_comments import ReviewCommentsStore
        from jig.store.threads import ThreadStore
        from jig.store.tickets import TicketStore

        store_dir = self._root / ".jig" / "store"
        store_dir.mkdir(parents=True, exist_ok=True)
        self._threads = ThreadStore(store_dir / "comments.jsonl")
        self._checkpoints = CheckpointStore(store_dir / "checkpoints.jsonl")
        self._memory = MemoryStore(store_dir)
        self._check_results = CheckResultsStore(store_dir / "check_results.jsonl")
        self._review_comments = ReviewCommentsStore(store_dir / "review_comments.jsonl")
        to_load = [
            self._threads.load(),
            self._checkpoints.load(),
            self._memory.load(),
            self._check_results.load(),
            self._review_comments.load(),
        ]
        if self._ticket_store is None:
            self._ticket_store = TicketStore(store_dir / "tickets.jsonl")
            to_load.append(self._ticket_store.load())
        await asyncio.gather(*to_load)

    @staticmethod
    def _require(store: "_StoreT | None", name: str) -> "_StoreT":
        if store is None:
            raise RuntimeError(
                f"StoreAuthority.load() must be called before accessing the "
                f"{name!r} port"
            )
        return store

    @property
    def tickets(self) -> "TicketStore":
        return self._require(self._ticket_store, "tickets")

    @property
    def threads(self) -> "ThreadStore":
        return self._require(self._threads, "threads")

    @property
    def memory(self) -> "MemoryStore":
        return self._require(self._memory, "memory")

    @property
    def checkpoints(self) -> "CheckpointStore":
        return self._require(self._checkpoints, "checkpoints")

    @property
    def check_results(self) -> "CheckResultsStore":
        return self._require(self._check_results, "check_results")

    @property
    def review_comments(self) -> "ReviewCommentsStore":
        return self._require(self._review_comments, "review_comments")

    async def write(self, uri: str, doc: dict[str, Any]) -> str:
        """Write ``doc`` to the artifact addressed by ``uri`` — through the
        security gate.

        Order: (1) parse/validate the URI — malformed, path-traversal, and
        out-of-set authorities all fail here via the parser's strict rules, plus
        a fragment-safety check the parser doesn't do; (2) authorize the
        authority against this instance's writable set; (3) persist: ``store``
        routes through ``TicketStore``; ``spec``/``arch``/``design``/``plan``
        raise ``UnimplementedAuthorityError`` until wired.
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
        if _RESERVED_KEY_ID.match(ticket_id):
            raise ProjectUriError(
                f"store-write ticket id {ticket_id!r} uses the reserved jig-N "
                "key namespace; ids and server-assigned keys must be disjoint"
            )
        body = self._prepare_body(ticket_id, doc)
        store = await self._tickets()
        # The create-vs-update decision is made atomically inside TicketStore
        # under the cross-process lock against fresh disk state — so a write can
        # never race a concurrent create into a spurious failure (partial body
        # failing create-validation, or a full body raising duplicate-id).
        await store.write_addressed(ticket_id, body)
        return ticket_id

    @staticmethod
    def _prepare_body(ticket_id: str, doc: dict[str, Any]) -> dict[str, Any]:
        """Strip the server-owned identity fields (``id``/``_id``, ``key``) from
        the write body and reject any attempt to set them.

        The id is the address, not a mutable field — a body naming a different id
        is a caller bug, surfaced loudly. The ``jig-N`` ``key`` is server-assigned
        and never accepted from a writer (a non-empty value is rejected; strip it
        so a full-ticket round-trip drops it rather than fighting the store)."""
        for id_key in ("id", "_id"):
            if id_key in doc and doc[id_key] != ticket_id:
                raise ProjectUriError(
                    f"ticket body {id_key}={doc[id_key]!r} conflicts with "
                    f"URI id {ticket_id!r}"
                )
        if doc.get("key"):
            raise ProjectUriError(
                f"ticket body key={doc['key']!r} cannot be set; the jig-N key is "
                "server-assigned"
            )
        return {k: v for k, v in doc.items() if k not in ("id", "_id", "key")}
