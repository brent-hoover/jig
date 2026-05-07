import pytest
from pydantic import ValidationError

from jig.ticket import Ticket, WorkType


def test_ticket_derived_from_defaults_none():
    t = Ticket(work_type=WorkType.FEATURE, title="t", created_by="cli")
    assert t.derived_from is None


def test_ticket_derived_from_accepts_uri():
    t = Ticket(
        work_type=WorkType.FEATURE,
        title="t",
        created_by="cli",
        derived_from="project://spec/capabilities/due-dates",
    )
    assert t.derived_from == "project://spec/capabilities/due-dates"


# ---- Block A.3: ``extra="forbid"`` + ``Field(default_factory=...)`` ------


def test_ticket_rejects_unknown_field():
    """A typo'd field name (``visulal_references``, ``laybels``) used to
    silently land on the model with ``extra='ignore'``; Block A.3 turns
    on ``extra='forbid'`` so the typo fails at construction time.
    """
    with pytest.raises(ValidationError, match="extra"):
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="cli",
            visulal_references=["dashboard"],  # type: ignore[call-arg]
        )


def test_ticket_default_collections_are_independent():
    """Field(default_factory=list) gives every Ticket its own list.

    Pre-Block-A.3 the default ``= []`` literal was actually safe in
    Pydantic v2 (it deepcopies under the hood) but the explicit factory
    makes the intent visible and lines up with the rest of the schemas
    package's discipline.
    """
    a = Ticket(work_type=WorkType.FEATURE, title="a", created_by="u")
    b = Ticket(work_type=WorkType.FEATURE, title="b", created_by="u")
    a.blocks.append("t-x")
    a.labels.append("lbl-a")
    a.capability_ids.append("cap-a")
    a.context_hints["k"] = "v"
    a.visual_references.append("vref-a")
    assert b.blocks == []
    assert b.labels == []
    assert b.capability_ids == []
    assert b.context_hints == {}
    assert b.visual_references == []


def test_ticket_legacy_field_migration_still_works():
    """``extra='forbid'`` + the ``mode='before'`` migrator must coexist:
    legacy ``type=`` is renamed to ``work_type`` *before* extra-key
    validation, so the rename keeps working.
    """
    t = Ticket(type="feature", title="t", created_by="cli")  # type: ignore[call-arg]
    assert t.work_type == WorkType.FEATURE
