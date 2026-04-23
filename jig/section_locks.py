"""Spec section locks — phase-scoped edit refusal per doc 16.

Phase 5 Task M. A section lock declares that once a given phase's
Handoff has been accepted on a ticket, further edits to the named
spec section are refused. The lock is phrased declaratively in the
work-type schema::

    section_locks:
      behaviors: {locked_after_phase: spec}
      acceptance_criteria: {locked_after_phase: spec}

and enforced at proposal-accept time. The shape lands in Phase 3F's
:class:`jig.work_types.WorkTypeSchema`; this module is where the
lock becomes policy.

The lock has two ingredients:

* **The schema** — which fields are locked, keyed by the phase whose
  handoff trips the lock.
* **The ticket's thread** — which phases have an accepted handoff.

The intersection of those two determines, at any given moment, which
fields on that ticket's spec are off-limits. A ticket without the
relevant handoff leaves everything editable; a handoff that does not
lock any section is a no-op on the lock map.

The enforcement path lives in ``proposal_mcp.handle_resolve_proposal``
— it calls :func:`locked_sections_for_ticket` on the accept branch
for ``ticket://spec`` / ``ticket://spec.<field>`` targets and refuses
any proposal whose affected fields intersect the lock map.
"""

from __future__ import annotations

from pathlib import Path

from jig.store.threads import ThreadStore
from jig.thread import Handoff
from jig.ticket import WorkType
from jig.work_types import WorkTypeSchema, load_work_type_schema


def locked_section_fields(schema: WorkTypeSchema) -> dict[str, str]:
    """Map section-field name → locking phase, from a schema.

    Reads ``schema.section_locks`` and extracts the ``locked_after_phase``
    value for each entry. Entries missing that key are skipped (future-
    proofing for other lock kinds doc 16 may add).
    """
    return {
        field: spec["locked_after_phase"]
        for field, spec in schema.section_locks.items()
        if "locked_after_phase" in spec
    }


async def accepted_handoff_phases(threads: ThreadStore, ticket_id: str) -> set[str]:
    """Phase names with an accepted Handoff on this ticket.

    Rejected handoffs don't trip locks (the phase didn't actually
    complete); pending handoffs don't trip locks (the evaluator
    hasn't signed off). Only ``acceptance_state == "accepted"``
    counts.
    """
    entries = await threads.find_by_kind(ticket_id, "handoff")
    return {
        e.phase
        for e in entries
        if isinstance(e, Handoff) and e.acceptance_state == "accepted"
    }


async def locked_sections_for_ticket(
    project_path: Path,
    threads: ThreadStore,
    ticket_id: str,
    work_type: WorkType,
) -> dict[str, str]:
    """Fields currently locked on this ticket's spec.

    Returns ``{field_name: locking_phase}`` for every schema-declared
    lock whose ``locked_after_phase`` has an accepted Handoff on the
    ticket. Empty map if the schema declares no locks or no matching
    handoff has landed yet.
    """
    schema = load_work_type_schema(project_path, work_type.value)
    locks = locked_section_fields(schema)
    if not locks:
        return {}
    accepted = await accepted_handoff_phases(threads, ticket_id)
    return {field: phase for field, phase in locks.items() if phase in accepted}


__all__ = [
    "accepted_handoff_phases",
    "locked_section_fields",
    "locked_sections_for_ticket",
]
