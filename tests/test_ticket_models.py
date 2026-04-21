from datetime import datetime

import pytest
from pydantic import ValidationError

from jig.ticket import Size, Ticket, TicketStatus, WorkType


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
    t = Ticket(work_type=WorkType.FEATURE, title="t", created_by="u")
    assert t.size == Size.M


def test_ticket_all_sizes_accepted() -> None:
    for size in Size:
        t = Ticket(
            work_type=WorkType.FEATURE, title="t", created_by="u", size=size
        )
        assert t.size == size


def test_ticket_rejects_unknown_work_type() -> None:
    with pytest.raises(ValidationError):
        Ticket(work_type="gossip", title="t", created_by="u")


def test_ticket_rejects_unknown_size() -> None:
    with pytest.raises(ValidationError):
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            size="enormous",
        )


def test_ticket_legacy_task_defaults_to_thread_workflow() -> None:
    """Pre-doc-03 `task` tickets always ran the thread dispatch loop.

    Migration must preserve that so in-flight data keeps behaving the
    way it did before the rename.
    """
    t = Ticket(type="task", title="t", created_by="u")
    assert t.work_type == WorkType.REFACTOR
    assert t.workflow == "thread"


def test_ticket_legacy_question_defaults_to_thread_workflow() -> None:
    t = Ticket(type="question", title="q", created_by="u")
    assert t.work_type == WorkType.FEATURE
    assert t.workflow == "thread"


def test_ticket_legacy_migration_respects_explicit_workflow() -> None:
    """If the caller already set `workflow`, don't override it."""
    t = Ticket(
        type="task", title="t", created_by="u", workflow="custom"
    )
    assert t.workflow == "custom"


def test_ticket_legacy_bug_keeps_default_workflow() -> None:
    """`bug` wasn't a thread type; migration must not touch workflow."""
    t = Ticket(type="bug", title="b", created_by="u")
    assert t.work_type == WorkType.BUGFIX
    assert t.workflow == "default"


