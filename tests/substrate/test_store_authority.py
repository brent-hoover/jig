"""Substrate — StoreAuthority routing (Epic 2).

The authority is a unified facade. ``read`` delegates to the existing
``project://`` resolver — store reads are wired (PR B); arch/design/plan still
surface ``UnimplementedAuthorityError``. ``write`` persists ``store`` through the
typed ``TicketStore`` (PR B2); the other authorities remain the declared seam.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jig.store.tickets import TicketStore
from jig.substrate.store_authority import StoreAuthority
from jig.uri.errors import (
    ProjectUriError,
    UnimplementedAuthorityError,
    UnknownAuthorityError,
)


def _doc(**overrides) -> dict:
    # A minimal valid ticket body. ``docs`` work_type is exempt from the AC
    # requirement, so title + created_by are all that's needed. The URI carries
    # the id, so it's deliberately absent here.
    base = {"work_type": "docs", "title": "Login", "created_by": "po"}
    base.update(overrides)
    return base


def test_authorities_are_the_five_project_authorities() -> None:
    assert StoreAuthority.AUTHORITIES == ("spec", "arch", "design", "plan", "store")


def test_authority_of_routes_uri_to_its_authority(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    assert sa.authority_of("project://spec/name") == "spec"
    assert sa.authority_of("project://store/tickets/jig-1") == "store"


def test_read_routes_unwired_authority_to_unimplemented(tmp_path) -> None:
    # store read is wired (PR B, #216); arch/design/plan remain follow-on.
    sa = StoreAuthority(tmp_path)
    for uri in (
        "project://arch/architecture",
        "project://design/frontend",
        "project://plan/build-plan",
    ):
        with pytest.raises(UnimplementedAuthorityError):
            sa.read(uri)


def test_read_rejects_unknown_authority(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises((UnknownAuthorityError, ProjectUriError)):
        sa.read("project://bogus/x")


async def test_write_creates_a_ticket_that_reads_back(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    returned = await sa.write("project://store/tickets/jig-1", _doc(title="Login"))
    assert returned == "jig-1"

    resolved = sa.read("project://store/tickets/jig-1")
    assert resolved.data["kind"] == "ticket"
    assert resolved.data["data"]["title"] == "Login"
    assert resolved.data["data"]["_id"] == "jig-1"
    # TicketStore.create assigns the jig-N handle — proof we routed through it,
    # not a raw dict append.
    assert resolved.data["data"]["key"] == "jig-1"


async def test_write_updates_an_existing_ticket(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    await sa.write("project://store/tickets/jig-1", _doc(title="Old"))
    await sa.write("project://store/tickets/jig-1", {"title": "New"})

    data = sa.read("project://store/tickets/jig-1").data["data"]
    assert data["title"] == "New"
    assert data["created_by"] == "po"  # untouched fields survive the merge


async def test_write_create_fires_the_store_create_callback(tmp_path) -> None:
    # Inject the orchestrator's already-wired store: the write must announce the
    # new ticket through the store's callback, not bypass it.
    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    created: list[str] = []
    store.set_create_callback(lambda t: created.append(t.id))

    sa = StoreAuthority(tmp_path, tickets=store)
    await sa.write("project://store/tickets/jig-1", _doc())
    await store.drain_background_tasks()

    assert created == ["jig-1"]


async def test_write_enforces_the_proposed_open_approval_gate(tmp_path) -> None:
    # PROPOSED -> OPEN is operator-only (TicketStore.approve); a generic write
    # cannot smuggle the transition.
    sa = StoreAuthority(tmp_path)
    await sa.write("project://store/tickets/jig-1", _doc(status="proposed"))
    with pytest.raises(ValueError, match="approval"):
        await sa.write("project://store/tickets/jig-1", {"status": "open"})


async def test_write_rejects_a_body_id_that_conflicts_with_the_uri(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ProjectUriError, match="conflicts"):
        await sa.write("project://store/tickets/jig-1", _doc(id="jig-2"))


async def test_write_to_the_ticket_list_is_rejected(tmp_path) -> None:
    # A write must address a single ticket, not the collection.
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ProjectUriError, match="single ticket"):
        await sa.write("project://store/tickets", _doc())


async def test_write_to_an_unwired_store_collection_raises(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises(UnimplementedAuthorityError):
        await sa.write("project://store/threads/jig-1", {"x": 1})


async def test_write_create_validates_the_schema(tmp_path) -> None:
    # Missing required fields (work_type/created_by) must fail at the model, not
    # land a malformed row on disk.
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ValidationError):
        await sa.write("project://store/tickets/jig-1", {"title": "no work_type"})
