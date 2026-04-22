"""Phase-tracking helpers shared by the orchestrator and the hooks runner.

Phase index is derived from SystemEvent(event_type='phase_run',
phase_result='success') records on the ticket — NOT stored as a field
on Ticket. Extracted from ``Orchestrator._current_phase_index`` so the
hooks runner can answer "what phase is this ticket on?" without
instantiating an orchestrator.
"""

from __future__ import annotations

from jig.models import WorkflowConfig
from jig.store.threads import ThreadStore


async def current_phase_index(
    threads: ThreadStore,
    ticket_id: str,
    workflow: WorkflowConfig,
) -> int:
    """Return the index of the first phase that has not yet succeeded.

    Returns ``len(workflow.phases)`` if every phase has succeeded.
    """
    events = await threads.find_by_kind(ticket_id, "system_event")
    succeeded: set[str] = set()
    for e in events:
        if getattr(e, "event_type", None) != "phase_run":
            continue
        if getattr(e, "phase_result", None) != "success":
            continue
        content = getattr(e, "content", "")
        # Recorded as "phase <name>: <status>".
        name = content.removeprefix("phase ").split(":")[0]
        succeeded.add(name)
    for phase_idx, phase in enumerate(workflow.phases):
        if phase.name not in succeeded:
            return phase_idx
    return len(workflow.phases)
