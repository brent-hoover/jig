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
