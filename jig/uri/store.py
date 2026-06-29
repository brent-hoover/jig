"""Helpers for ``project://store/...`` URIs (runtime stores).

Store URIs are resolved by :meth:`jig.substrate.store_authority.StoreAuthority.read`,
which projects over the *typed* stores (``TicketStore`` etc.) — the same instances
the engines use — rather than a parallel JSONL replay. This module keeps only the
URI-shape validation and the canonical serialization the read path shares; there is
no second op-log replay here (that would be the drift ADR-0001 forbids).

PR B (#216) wires the canonical ``tickets`` collection — ``_id``-keyed, the simplest
shape. Threads/messages/events/checkpoints have different keying and land in a
follow-on; they raise ``UnimplementedAuthorityError`` for now.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import ProjectUri

if TYPE_CHECKING:
    from jig.ticket import Ticket

# Store collections the read path resolves. PR B wires ``tickets`` only.
_WIRED_COLLECTIONS: frozenset[str] = frozenset({"tickets"})


def reject_unsupported_store_uri(uri: ProjectUri) -> None:
    """Raise for store URIs the read path doesn't resolve.

    Rejects: unwired collections (only ``tickets``), sub-document paths,
    fragments, and ``@revision`` pins (resolving the latest while reporting a
    pinned revision would mislead the caller).
    """
    collection = uri.path[0] if uri.path else None
    if collection not in _WIRED_COLLECTIONS:
        raise UnimplementedAuthorityError(
            f"store collection {collection!r} not yet wired; got {uri!r}"
        )
    if uri.fragment is not None or len(uri.path) > 2:
        raise UnimplementedAuthorityError(
            f"store sub-addressing not yet wired; got {uri!r}"
        )
    if uri.revision is not None:
        raise UnimplementedAuthorityError(
            f"store @revision pinning not yet wired; got {uri!r}"
        )


def serialize_ticket(ticket: "Ticket") -> dict[str, Any]:
    """The canonical dict a URI read returns for a ticket — the same serialization
    ``TypedCollection`` persists (``by_alias``, json mode), so the URI door and the
    typed store never disagree on shape."""
    return ticket.model_dump(mode="json", by_alias=True)
