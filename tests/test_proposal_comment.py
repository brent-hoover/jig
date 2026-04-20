"""Tests for the proposal-typed Comment extension (Phase 3 Task E).

Minimum coverage that matters: the new kind literal is accepted, the
new payload fields roundtrip through model_dump, and non-proposal
Comments still deserialize untouched.
"""

from pathlib import Path

import pytest

from jig.ticket import Comment


def test_proposal_kind_accepted() -> None:
    c = Comment(
        ticket_id="t-1",
        author="alice",
        content="flip the default",
        kind="proposal",
        proposal_target="ticket://spec.behaviors",
        proposal_section=None,
        proposal_change="summary: new",
        proposal_state="pending",
        proposal_owners=["po"],
    )
    assert c.kind == "proposal"
    assert c.proposal_state == "pending"
    assert c.proposal_owners == ["po"]


def test_comment_without_proposal_fields_defaults_cleanly() -> None:
    c = Comment(ticket_id="t-1", author="alice", content="just a note")
    assert c.kind == "comment"
    assert c.proposal_target is None
    assert c.proposal_state is None
    assert c.proposal_owners == []


def test_roundtrip_through_dump_and_load() -> None:
    original = Comment(
        ticket_id="t-1",
        author="alice",
        content="propose",
        kind="proposal",
        proposal_target="ticket://spec.behaviors",
        proposal_change="behaviors:\n- id: B2",
        proposal_state="pending",
        proposal_owners=["po", "sa"],
        proposal_spec_version=3,
    )
    raw = original.model_dump(mode="json", by_alias=True)
    reloaded = Comment.model_validate(raw)
    assert reloaded.kind == "proposal"
    assert reloaded.proposal_target == "ticket://spec.behaviors"
    assert reloaded.proposal_owners == ["po", "sa"]
    assert reloaded.proposal_spec_version == 3


def test_invalid_proposal_state_rejected() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Comment(
            ticket_id="t-1",
            author="alice",
            content="x",
            kind="proposal",
            proposal_state="mumbling",  # not in the Literal set
        )


def test_resolver_entry_references_parent(tmp_path: Path) -> None:
    """Proposal resolutions link back to the originating entry."""
    original = Comment(
        ticket_id="t-1", author="alice", content="propose", kind="proposal"
    )
    resolver = Comment(
        ticket_id="t-1",
        author="bob",
        content="ok",
        kind="proposal",
        proposal_parent_id=original.id,
        proposal_state="accepted",
    )
    assert resolver.proposal_parent_id == original.id
