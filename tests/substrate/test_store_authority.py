"""Substrate bones — StoreAuthority routing (Epic 2, task 2).

Bones phase: the authority is a unified facade. ``read`` delegates to the
existing ``project://`` resolver — so it routes correctly today, and honestly
surfaces ``UnimplementedAuthorityError`` for the authorities not yet wired
(arch/design/plan/store). ``write`` is the declared seam MVP fills in.
"""

from __future__ import annotations

import pytest

from jig.substrate.store_authority import StoreAuthority
from jig.uri.errors import (
    ProjectUriError,
    UnimplementedAuthorityError,
    UnknownAuthorityError,
)


def test_authorities_are_the_five_project_authorities() -> None:
    assert StoreAuthority.AUTHORITIES == ("spec", "arch", "design", "plan", "store")


def test_authority_of_routes_uri_to_its_authority(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    assert sa.authority_of("project://spec/name") == "spec"
    assert sa.authority_of("project://store/tickets/jig-1") == "store"


def test_read_routes_unwired_authority_to_unimplemented(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    for uri in (
        "project://arch/architecture",
        "project://design/frontend",
        "project://plan/build-plan",
        "project://store/tickets/jig-1",
    ):
        with pytest.raises(UnimplementedAuthorityError):
            sa.read(uri)


def test_read_rejects_unknown_authority(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises((UnknownAuthorityError, ProjectUriError)):
        sa.read("project://bogus/x")


async def test_write_is_the_declared_bones_seam(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises(UnimplementedAuthorityError):
        await sa.write("project://store/tickets/jig-1", {"id": "jig-1"})
