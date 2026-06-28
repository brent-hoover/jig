"""Substrate MVP (#216, PR B) — resolve the ``project://store/...`` read path.

``resolve_store_uri`` reads the runtime JSONL stores. PR B wires the canonical
``tickets`` collection (``_id``-keyed); threads/events/etc. (different keying)
are a follow-on. Returns ``{kind, data}`` for the resolver to wrap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.store.collection import Collection
from jig.ticket import Ticket
from jig.uri.errors import UnimplementedAuthorityError
from jig.uri.parser import parse_project_uri
from jig.uri.store import resolve_store_uri


def _ticket_row(ticket_id: str, title: str = "A") -> dict:
    # A valid Ticket (docs work_type is exempt from the AC requirement), in the
    # serialized form the store persists.
    return Ticket(
        id=ticket_id, title=title, work_type="docs", created_by="po"
    ).model_dump(mode="json", by_alias=True)


async def _seed_ticket(project_root: Path, row: dict) -> None:
    store = Collection(project_root / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    await store.insert(row)


async def test_resolve_a_single_ticket(tmp_path: Path) -> None:
    await _seed_ticket(tmp_path, _ticket_row("jig-1", title="Login"))

    result = resolve_store_uri(
        parse_project_uri("project://store/tickets/jig-1"), tmp_path
    )

    assert result["kind"] == "ticket"
    assert result["data"]["title"] == "Login"
    # Normalized through the Ticket model — defaulted fields are present.
    assert "work_type" in result["data"]
    assert result["data"]["_id"] == "jig-1"


async def test_resolve_a_missing_ticket_returns_none_data(tmp_path: Path) -> None:
    await _seed_ticket(tmp_path, _ticket_row("jig-1", title="Login"))

    result = resolve_store_uri(
        parse_project_uri("project://store/tickets/ghost"), tmp_path
    )

    assert result["data"] is None


async def test_resolve_the_ticket_list(tmp_path: Path) -> None:
    await _seed_ticket(tmp_path, _ticket_row("jig-1", title="A"))
    await _seed_ticket(tmp_path, _ticket_row("jig-2", title="B"))

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


def test_ticket_revision_pin_raises(tmp_path: Path) -> None:
    # @revision pinning isn't materialized; resolving the latest while reporting
    # a pinned revision would mislead the caller, so reject it.
    with pytest.raises(UnimplementedAuthorityError):
        resolve_store_uri(
            parse_project_uri("project://store/tickets/jig-1@revision:3"), tmp_path
        )
