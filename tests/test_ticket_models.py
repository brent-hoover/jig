from datetime import datetime

import pytest
from pydantic import ValidationError

from jig.ticket import Comment, Ticket, TicketStatus, TicketType


def test_ticket_defaults() -> None:
    t = Ticket(
        type=TicketType.FEATURE,
        title="add search",
        created_by="user",
    )
    assert t.status == TicketStatus.OPEN
    assert t.description == ""
    assert t.assignee is None
    assert t.parent_id is None
    assert t.blocks == []
    assert t.blocked_by == []
    assert t.labels == []
    assert isinstance(t.created_at, datetime)
    assert isinstance(t.updated_at, datetime)


def test_ticket_all_statuses_accepted() -> None:
    for status in TicketStatus:
        t = Ticket(
            type=TicketType.TASK,
            title="t",
            created_by="orchestrator",
            status=status,
        )
        assert t.status == status


def test_ticket_all_types_accepted() -> None:
    for ttype in TicketType:
        t = Ticket(type=ttype, title="t", created_by="u")
        assert t.type == ttype


def test_comment_default_kind() -> None:
    c = Comment(ticket_id="T-1", author="dev", content="hello")
    assert c.kind == "comment"
    assert c.commit_sha is None
    assert c.phase_result is None
    assert c.phase_branch is None


def test_comment_phase_run() -> None:
    c = Comment(
        ticket_id="T-1",
        author="orchestrator",
        content="phase done",
        kind="phase_run",
        phase_result="success",
        phase_branch="jig/T-1",
    )
    assert c.kind == "phase_run"
    assert c.phase_result == "success"


def test_comment_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        Comment(ticket_id="T-1", author="dev", content="x", kind="gossip")


def test_comment_rejects_unknown_phase_result() -> None:
    with pytest.raises(ValidationError):
        Comment(
            ticket_id="T-1",
            author="o",
            content="x",
            kind="phase_run",
            phase_result="partial",
        )
