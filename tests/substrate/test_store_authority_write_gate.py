"""Substrate MVP (#216, task 4) — the StoreAuthority.write() security gate.

The reviewable security model that gates production routing: every write()
parses + validates the URI (rejecting malformed/traversal URIs and authorities
outside the closed set) and enforces authorization-by-authority — a StoreAuthority
may be scoped to a set of writable authorities, and a write outside that set is
rejected with a typed error, never silently coerced.

Persistence is deferred (PR B); a write that passes the gate raises
``UnimplementedAuthorityError`` for now.
"""

from __future__ import annotations

import pytest

from jig.substrate.store_authority import StoreAuthority, WriteNotAuthorizedError
from jig.uri.errors import (
    ProjectUriError,
    UnimplementedAuthorityError,
    UnknownAuthorityError,
)


# --- path safety / malformed URIs (enforced via the parser) ------------------


async def test_write_rejects_a_malformed_uri(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ProjectUriError):
        await sa.write("not-a-project-uri", {"x": 1})


async def test_write_rejects_path_traversal(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises(ProjectUriError):
        await sa.write("project://spec/../arch/secret", {"x": 1})


async def test_write_rejects_an_unknown_authority(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)
    with pytest.raises(UnknownAuthorityError):
        await sa.write("project://bogus/x", {"x": 1})


# --- authorization-by-authority ----------------------------------------------


async def test_write_to_a_non_writable_authority_is_rejected(tmp_path) -> None:
    # Scoped to spec only (e.g. the Discovery engine's StoreAuthority).
    sa = StoreAuthority(tmp_path, writable=("spec",))

    with pytest.raises(WriteNotAuthorizedError) as exc:
        await sa.write("project://arch/architecture", {"x": 1})
    assert "arch" in str(exc.value)


async def test_write_to_a_writable_authority_passes_the_gate(tmp_path) -> None:
    sa = StoreAuthority(tmp_path, writable=("spec",))
    # Passes authorization; persistence not wired yet (PR B).
    with pytest.raises(UnimplementedAuthorityError):
        await sa.write("project://spec/name", {"x": 1})


async def test_default_writable_is_all_authorities(tmp_path) -> None:
    sa = StoreAuthority(tmp_path)  # no writable -> all 5
    for authority in StoreAuthority.AUTHORITIES:
        with pytest.raises(UnimplementedAuthorityError):
            await sa.write(f"project://{authority}/x", {"x": 1})


def test_construction_rejects_a_writable_authority_outside_the_closed_set(
    tmp_path,
) -> None:
    with pytest.raises(ValueError, match="not a valid authority"):
        StoreAuthority(tmp_path, writable=("spec", "bogus"))
