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
    returned = await sa.write("project://store/tickets/login", _doc(title="Login"))
    assert returned == "login"

    resolved = sa.read("project://store/tickets/login")
    assert resolved.data["kind"] == "ticket"
    assert resolved.data["data"]["title"] == "Login"
    assert resolved.data["data"]["_id"] == "login"
    # TicketStore.create assigns the jig-N handle — proof we routed through it,
    # not a raw dict append. (The key namespace stays disjoint from the id.)
    assert resolved.data["data"]["key"] == "jig-1"


async def test_write_updates_an_existing_ticket(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    await sa.write("project://store/tickets/login", _doc(title="Old"))
    await sa.write("project://store/tickets/login", {"title": "New"})

    data = sa.read("project://store/tickets/login").data["data"]
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
    await sa.write("project://store/tickets/login", _doc())
    await store.drain_background_tasks()

    assert created == ["login"]


async def test_write_enforces_the_proposed_open_approval_gate(tmp_path) -> None:
    # PROPOSED -> OPEN is operator-only (TicketStore.approve); a generic write
    # cannot smuggle the transition.
    sa = StoreAuthority(tmp_path)
    await sa.write("project://store/tickets/login", _doc(status="proposed"))
    with pytest.raises(ValueError, match="approval"):
        await sa.write("project://store/tickets/login", {"status": "open"})


async def test_write_rejects_a_body_id_that_conflicts_with_the_uri(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ProjectUriError, match="conflicts"):
        await sa.write("project://store/tickets/login", _doc(id="signup"))


async def test_write_rejects_a_jig_key_shaped_id(tmp_path) -> None:
    # A URI id in the reserved jig-N key namespace would shadow a server key in
    # resolve_ref; reject it so ids and keys stay disjoint.
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ProjectUriError, match="reserved jig-N"):
        await sa.write("project://store/tickets/jig-1", _doc())


async def test_write_to_the_ticket_list_is_rejected(tmp_path) -> None:
    # A write must address a single ticket, not the collection.
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ProjectUriError, match="single ticket"):
        await sa.write("project://store/tickets", _doc())


async def test_write_to_an_unwired_store_collection_raises(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises(UnimplementedAuthorityError):
        await sa.write("project://store/threads/login", {"x": 1})


async def test_write_through_a_stale_injected_store_still_updates(tmp_path) -> None:
    # An injected store that loaded before another process created the ticket is
    # also stale; the write must still reload and UPDATE, not take the create
    # branch. (Covers the injected case, not just owned stores.)
    injected = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await injected.load()  # empty snapshot

    other = StoreAuthority(tmp_path)  # a separate "process"
    await other.write("project://store/tickets/login", _doc(title="From other"))

    sa = StoreAuthority(tmp_path, tickets=injected)  # injected store is now stale
    await sa.write("project://store/tickets/login", {"title": "Updated via injected"})
    assert (
        sa.read("project://store/tickets/login").data["data"]["title"]
        == "Updated via injected"
    )


async def test_write_create_validates_the_schema(tmp_path) -> None:
    # Missing required fields (work_type/created_by) must fail at the model, not
    # land a malformed row on disk.
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ValidationError):
        await sa.write("project://store/tickets/login", {"title": "no work_type"})


async def test_write_create_rejects_a_caller_supplied_key(tmp_path) -> None:
    # The jig-N key is server-assigned; a create body cannot preset it (that
    # would let a caller forge or duplicate keys, breaking the counter).
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ProjectUriError, match="key"):
        await sa.write("project://store/tickets/login", _doc(key="jig-999"))


async def test_write_update_cannot_change_the_key_but_may_echo_it(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    await sa.write("project://store/tickets/login", _doc(title="A"))  # gets key jig-1
    # Echoing the assigned key is fine (read-modify-write round-trip).
    await sa.write("project://store/tickets/login", {"key": "jig-1", "title": "B"})
    assert sa.read("project://store/tickets/login").data["data"]["title"] == "B"
    # Changing it is rejected.
    with pytest.raises(ProjectUriError, match="key"):
        await sa.write("project://store/tickets/login", {"key": "jig-2"})


async def test_write_to_a_ticket_another_instance_created_updates_it(tmp_path) -> None:
    # Two StoreAuthority instances on one path (i.e. two processes). sa2's
    # in-memory snapshot predates login's creation by sa1; a write to login must
    # reload and UPDATE the existing on-disk ticket, not wrongly take the create
    # branch (which would spuriously fail).
    sa1 = StoreAuthority(tmp_path)
    sa2 = StoreAuthority(tmp_path)
    await sa2.write("project://store/tickets/other", _doc())  # sa2 snapshot: {other}
    await sa1.write("project://store/tickets/login", _doc(title="From sa1"))

    await sa2.write("project://store/tickets/login", {"title": "Updated by sa2"})
    assert (
        sa1.read("project://store/tickets/login").data["data"]["title"]
        == "Updated by sa2"
    )
