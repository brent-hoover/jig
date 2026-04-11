import asyncio
from pathlib import Path

import pytest

from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ticket import Ticket, TicketStatus, TicketType
from jig.store.tickets import TicketStore


@pytest.mark.asyncio
async def test_orchestrator_startup_loads_collections(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    try:
        assert orch.tickets is not None
        assert orch.comments is not None
        assert orch.bus is not None
        assert orch._live_subscribers == {}
    finally:
        await orch.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_resumes_in_progress_tickets(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )
    ts = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await ts.load()
    tid = await ts.create(
        Ticket(
            type=TicketType.FEATURE,
            title="f",
            created_by="user",
            status=TicketStatus.IN_PROGRESS,
        )
    )

    orch = Orchestrator(project_path=tmp_path)
    seen: list[str] = []

    async def fake_run_ticket(ticket_id: str) -> None:
        seen.append(ticket_id)

    orch._run_ticket = fake_run_ticket  # type: ignore[method-assign]

    await orch.startup()
    try:
        await asyncio.sleep(0.05)
        assert seen == [tid]
    finally:
        await orch.shutdown()
