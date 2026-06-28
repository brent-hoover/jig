"""Substrate MVP (#216, PR B) — resolve the ``project://store/...`` read path.

``resolve_store_uri`` reads the runtime JSONL stores. PR B wires the canonical
``tickets`` collection (``_id``-keyed); threads/events/etc. (different keying)
are a follow-on. Returns ``{kind, data}`` for the resolver to wrap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.store.collection import Collection
from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import parse_project_uri
from jig.uri.store import resolve_store_uri


async def _seed_ticket(project_root: Path, ticket: dict) -> None:
    store = Collection(project_root / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    await store.insert(ticket)


async def test_resolve_a_single_ticket(tmp_path: Path) -> None:
    await _seed_ticket(tmp_path, {"_id": "jig-1", "title": "Login"})

    result = resolve_store_uri(
        parse_project_uri("project://store/tickets/jig-1"), tmp_path
    )

    assert result["kind"] == "ticket"
    assert result["data"]["title"] == "Login"


async def test_resolve_a_missing_ticket_returns_none_data(tmp_path: Path) -> None:
    await _seed_ticket(tmp_path, {"_id": "jig-1", "title": "Login"})

    result = resolve_store_uri(
        parse_project_uri("project://store/tickets/ghost"), tmp_path
    )

    assert result["data"] is None


async def test_resolve_the_ticket_list(tmp_path: Path) -> None:
    await _seed_ticket(tmp_path, {"_id": "jig-1", "title": "A"})
    await _seed_ticket(tmp_path, {"_id": "jig-2", "title": "B"})

    result = resolve_store_uri(parse_project_uri("project://store/tickets"), tmp_path)

    assert result["kind"] == "ticket_list"
    assert {t["_id"] for t in result["data"]} == {"jig-1", "jig-2"}


def test_resolve_an_empty_store_is_not_an_error(tmp_path: Path) -> None:
    # No tickets.jsonl yet -> empty list, not a crash.
    result = resolve_store_uri(parse_project_uri("project://store/tickets"), tmp_path)
    assert result == {"kind": "ticket_list", "data": []}


def test_unwired_store_collection_raises(tmp_path: Path) -> None:
    # threads/events/checkpoints land in a follow-on PR.
    with pytest.raises(UnimplementedAuthorityError):
        resolve_store_uri(parse_project_uri("project://store/threads/jig-1"), tmp_path)


def test_deeper_ticket_subpath_raises_instead_of_resolving_parent(
    tmp_path: Path,
) -> None:
    # A sub-document path must NOT silently resolve to the parent ticket.
    with pytest.raises(UnimplementedAuthorityError):
        resolve_store_uri(
            parse_project_uri("project://store/tickets/jig-1/entries/e1"), tmp_path
        )


def test_ticket_fragment_raises(tmp_path: Path) -> None:
    with pytest.raises(UnimplementedAuthorityError):
        resolve_store_uri(
            parse_project_uri("project://store/tickets/jig-1#sub"), tmp_path
        )
