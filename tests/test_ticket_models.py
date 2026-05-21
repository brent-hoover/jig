from datetime import datetime

import pytest
from pydantic import ValidationError

from jig.ticket import Size, Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def test_ticket_defaults() -> None:
    t = Ticket(
        work_type=WorkType.FEATURE,
        title="add search",
        created_by="user",
        description=TICKET_AC_PLACEHOLDER,
    )
    assert t.status == TicketStatus.OPEN
    assert t.description == TICKET_AC_PLACEHOLDER
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
            description=TICKET_AC_PLACEHOLDER,
        )
        assert t.status == status


def test_ticket_all_work_types_accepted() -> None:
    # Work types require AC; system types don't. Use the placeholder for
    # both so the loop doesn't need to care which side a value lands on.
    for wt in WorkType:
        t = Ticket(
            work_type=wt,
            title="t",
            created_by="u",
            description=TICKET_AC_PLACEHOLDER,
        )
        assert t.work_type == wt


def test_ticket_accepts_legacy_type_kwarg() -> None:
    """Transitional: the old `type` kwarg with pre-doc-03 values still loads."""
    t = Ticket(type="bug", title="b", created_by="u", description=TICKET_AC_PLACEHOLDER)
    assert t.work_type == WorkType.BUGFIX

    t2 = Ticket(
        type="chore", title="c", created_by="u", description=TICKET_AC_PLACEHOLDER
    )
    assert t2.work_type == WorkType.REFACTOR

    t3 = Ticket(
        type="task", title="t", created_by="u", description=TICKET_AC_PLACEHOLDER
    )
    assert t3.work_type == WorkType.REFACTOR

    t4 = Ticket(
        type="question", title="q", created_by="u", description=TICKET_AC_PLACEHOLDER
    )
    assert t4.work_type == WorkType.FEATURE


def test_ticket_size_defaults_to_medium() -> None:
    t = Ticket(
        work_type=WorkType.FEATURE,
        title="t",
        created_by="u",
        description=TICKET_AC_PLACEHOLDER,
    )
    assert t.size == Size.M


def test_ticket_all_sizes_accepted() -> None:
    for size in Size:
        t = Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            size=size,
            description=TICKET_AC_PLACEHOLDER,
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
            description=TICKET_AC_PLACEHOLDER,
        )


def test_ticket_legacy_task_defaults_to_thread_workflow() -> None:
    """Pre-doc-03 `task` tickets always ran the thread dispatch loop.

    Migration must preserve that so in-flight data keeps behaving the
    way it did before the rename.
    """
    t = Ticket(
        type="task", title="t", created_by="u", description=TICKET_AC_PLACEHOLDER
    )
    assert t.work_type == WorkType.REFACTOR
    assert t.workflow == "thread"


def test_ticket_legacy_question_defaults_to_thread_workflow() -> None:
    t = Ticket(
        type="question", title="q", created_by="u", description=TICKET_AC_PLACEHOLDER
    )
    assert t.work_type == WorkType.FEATURE
    assert t.workflow == "thread"


def test_ticket_legacy_migration_respects_explicit_workflow() -> None:
    """If the caller already set `workflow`, don't override it."""
    t = Ticket(
        type="task",
        title="t",
        created_by="u",
        workflow="custom",
        description=TICKET_AC_PLACEHOLDER,
    )
    assert t.workflow == "custom"


def test_ticket_legacy_bug_keeps_default_workflow() -> None:
    """`bug` wasn't a thread type; migration must not touch workflow."""
    t = Ticket(type="bug", title="b", created_by="u", description=TICKET_AC_PLACEHOLDER)
    assert t.work_type == WorkType.BUGFIX
    assert t.workflow == "default"


# ---------------------------------------------------------------------------
# Ticket.id is path-safe — Block 1 critical 2.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ticket_id",
    [
        "tb-001",
        "t_007",
        "spike-r-shopify-delta",
        "ticket-007",
        "1abc",
    ],
)
def test_ticket_id_accepts_canonical_shapes(ticket_id: str) -> None:
    t = Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="t",
        created_by="u",
        description=TICKET_AC_PLACEHOLDER,
    )
    assert t.id == ticket_id


@pytest.mark.parametrize(
    "ticket_id",
    [
        "../etc/passwd",
        "..",
        "TB-001",  # uppercase
        "tb 001",  # space
        "-leading-dash",
        ".hidden",
        "tb.001",  # dot in id
        "tb/001",  # slash
        "tb\\001",  # backslash
        "",  # empty string
        "x" * 101,  # over the cap
    ],
)
def test_ticket_id_rejects_unsafe_inputs(ticket_id: str) -> None:
    with pytest.raises(ValidationError):
        Ticket(
            id=ticket_id,
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            description=TICKET_AC_PLACEHOLDER,
        )


def test_ticket_id_validation_error_names_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Ticket(
            id="../escape",
            work_type=WorkType.FEATURE,
            title="t",
            created_by="u",
            description=TICKET_AC_PLACEHOLDER,
        )
    # The validator's ValueError.message should mention "Ticket.id"
    # so operators can trace which field rejected the input.
    assert "Ticket.id" in str(exc_info.value)
