from datetime import datetime

import pytest
from pydantic import ValidationError

from jig.ticket import Comment, Ticket, TicketStatus, WorkType


def test_ticket_defaults() -> None:
    t = Ticket(
        work_type=WorkType.FEATURE,
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
            work_type=WorkType.REFACTOR,
            title="t",
            created_by="orchestrator",
            status=status,
        )
        assert t.status == status


def test_ticket_all_work_types_accepted() -> None:
    for wt in WorkType:
        t = Ticket(work_type=wt, title="t", created_by="u")
        assert t.work_type == wt


def test_ticket_accepts_legacy_type_kwarg() -> None:
    """Transitional: the old `type` kwarg with pre-doc-03 values still loads."""
    t = Ticket(type="bug", title="b", created_by="u")
    assert t.work_type == WorkType.BUGFIX

    t2 = Ticket(type="chore", title="c", created_by="u")
    assert t2.work_type == WorkType.REFACTOR

    t3 = Ticket(type="task", title="t", created_by="u")
    assert t3.work_type == WorkType.REFACTOR

    t4 = Ticket(type="question", title="q", created_by="u")
    assert t4.work_type == WorkType.FEATURE


def test_ticket_size_defaults_to_medium() -> None:
    from jig.ticket import Size

    t = Ticket(work_type=WorkType.FEATURE, title="t", created_by="u")
    assert t.size == Size.M


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
