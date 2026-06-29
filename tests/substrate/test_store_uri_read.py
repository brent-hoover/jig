"""``project://store/...`` read resolution via ``StoreAuthority.read`` (ADR-0001).

Store reads project over the typed ``TicketStore`` — the same store the engines
use — rather than a parallel JSONL replay. These tests drive the read through a
fresh ``StoreAuthority`` over data persisted by the canonical store, proving the
URI door reflects persisted state and surfaces store corruption via the one
canonical load (no duplicate replay).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.store.tickets import TicketStore
from jig.substrate.store_authority import StoreAuthority
from jig.ticket import Ticket
from jig.uri.errors import UnimplementedAuthorityError


def _ticket(ticket_id: str, title: str = "A") -> Ticket:
    # ``docs`` work_type is exempt from the AC requirement.
    return Ticket(id=ticket_id, title=title, work_type="docs", created_by="po")


async def _seed(project_root: Path, *tickets: Ticket) -> None:
    store = TicketStore(project_root / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    for t in tickets:
        await store.create(t)


async def test_resolve_a_single_ticket(tmp_path: Path) -> None:
    await _seed(tmp_path, _ticket("alpha", title="Login"))

    result = (await StoreAuthority(tmp_path).read("project://store/tickets/alpha")).data

    assert result["kind"] == "ticket"
    assert result["data"]["title"] == "Login"
    # Serialized through the Ticket model — defaulted fields + alias present.
    assert "work_type" in result["data"]
    assert result["data"]["_id"] == "alpha"


async def test_resolve_a_missing_ticket_returns_none_data(tmp_path: Path) -> None:
    await _seed(tmp_path, _ticket("alpha"))

    result = (await StoreAuthority(tmp_path).read("project://store/tickets/ghost")).data

    assert result["data"] is None


async def test_resolve_the_ticket_list(tmp_path: Path) -> None:
    await _seed(tmp_path, _ticket("alpha"), _ticket("beta"))

    result = (await StoreAuthority(tmp_path).read("project://store/tickets")).data

    assert result["kind"] == "ticket_list"
    assert {t["_id"] for t in result["data"]} == {"alpha", "beta"}


async def test_resolve_an_empty_store_is_not_an_error(tmp_path: Path) -> None:
    # No tickets.jsonl yet -> empty list, not a crash.
    result = (await StoreAuthority(tmp_path).read("project://store/tickets")).data
    assert result == {"kind": "ticket_list", "data": []}


async def test_unwired_store_collection_raises(tmp_path: Path) -> None:
    # threads/events/checkpoints land in a follow-on PR.
    with pytest.raises(UnimplementedAuthorityError):
        await StoreAuthority(tmp_path).read("project://store/threads/x")


async def test_deeper_ticket_subpath_raises_instead_of_resolving_parent(
    tmp_path: Path,
) -> None:
    # A sub-document path must NOT silently resolve to the parent ticket.
    with pytest.raises(UnimplementedAuthorityError):
        await StoreAuthority(tmp_path).read("project://store/tickets/alpha/entries/e1")


async def test_ticket_fragment_raises(tmp_path: Path) -> None:
    with pytest.raises(UnimplementedAuthorityError):
        await StoreAuthority(tmp_path).read("project://store/tickets/alpha#sub")


async def test_ticket_revision_pin_raises(tmp_path: Path) -> None:
    # @revision pinning isn't materialized; resolving the latest while reporting
    # a pinned revision would mislead the caller, so reject it.
    with pytest.raises(UnimplementedAuthorityError):
        await StoreAuthority(tmp_path).read("project://store/tickets/alpha@revision:3")


def _write_oplog(project_root: Path, *lines: str) -> None:
    path = project_root / ".jig" / "store" / "tickets.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines))


async def test_read_surfaces_a_corrupt_oplog_via_the_canonical_load(
    tmp_path: Path,
) -> None:
    # A corrupt op-log fails on the typed store's load (one canonical replay),
    # surfaced through the read rather than masked.
    _write_oplog(tmp_path, '{"_op": "update", "_id": "x", "title": "X"}')
    with pytest.raises(ValueError, match="update for unknown id"):
        await StoreAuthority(tmp_path).read("project://store/tickets")
