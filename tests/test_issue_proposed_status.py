"""Front-door issues land in a non-dispatchable PROPOSED state.

``find_ready`` is OPEN-only, so a PROPOSED ticket must never be returned as
ready for dispatch even when it has a top-level work_type and no blockers.
"""

from pathlib import Path

import pytest

from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.mark.asyncio
async def test_proposed_ticket_is_not_ready(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()

    open_id = await store.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="open",
            created_by="u",
            description=TICKET_AC_PLACEHOLDER,
            status=TicketStatus.OPEN,
        )
    )
    await store.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="proposed",
            created_by="u",
            description=TICKET_AC_PLACEHOLDER,
            status=TicketStatus.PROPOSED,
        )
    )

    ready = await store.find_ready()

    ready_ids = {t.id for t in ready}
    assert open_id in ready_ids
    assert all(t.status is not TicketStatus.PROPOSED for t in ready)
