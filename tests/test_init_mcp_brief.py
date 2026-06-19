"""brief_* MCP tool handlers."""

import pytest

from jig.init_mcp import (
    handle_brief_get_section,
    handle_brief_list_sections,
    handle_brief_set_section,
    handle_po_finish_brief,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


@pytest.fixture
async def wired(tmp_path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    brief_path = tmp_path / "docs" / "brief.md"
    brief_path.write_text(
        "# myproj\n\n"
        "Intro paragraph.\n\n"
        "## Built\n\n- A capability\n\n"
        "## Non-goals\n\n- Time tracking\n"
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    brief = Ticket(
        id="brief",
        work_type=WorkType.BRIEF,
        title="Brief",
        created_by="cli",
    )
    await tickets.create(brief)
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "brief_path": brief_path,
        "project_path": tmp_path,
    }


@pytest.mark.asyncio
async def test_brief_list_sections_returns_in_order(wired):
    result = await handle_brief_list_sections(project_path=wired["project_path"])
    assert result == ["Built", "Non-goals"]


@pytest.mark.asyncio
async def test_brief_get_section_returns_body(wired):
    body = await handle_brief_get_section(
        project_path=wired["project_path"],
        name="Built",
    )
    assert "A capability" in body


@pytest.mark.asyncio
async def test_brief_get_section_missing_raises(wired):
    with pytest.raises(KeyError):
        await handle_brief_get_section(
            project_path=wired["project_path"],
            name="Backlog",
        )


@pytest.mark.asyncio
async def test_brief_set_section_replaces(wired):
    await handle_brief_set_section(
        project_path=wired["project_path"],
        name="Non-goals",
        markdown="- Time tracking\n- Sharing\n",
    )
    text = wired["brief_path"].read_text()
    assert "- Sharing" in text


@pytest.mark.asyncio
async def test_brief_set_section_appends_when_missing(wired):
    await handle_brief_set_section(
        project_path=wired["project_path"],
        name="Backlog",
        markdown="- Mobile\n",
    )
    text = wired["brief_path"].read_text()
    assert "## Backlog" in text
    assert "- Mobile" in text


@pytest.mark.asyncio
async def test_po_finish_brief_emits_handoff(wired):
    await handle_po_finish_brief(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        summary="Brief complete.",
        author="po",
    )
    entries = await wired["threads"].for_ticket("brief")
    handoffs = [e for e in entries if e.kind == "handoff"]
    assert len(handoffs) == 1
    assert handoffs[0].phase == "spec-generator"
    assert handoffs[0].summary == "Brief complete."
    # Brief is now resolved so the PO agent's exit-on-terminal-status
    # poll fires and the agent loop returns instead of hanging.
    brief = await wired["tickets"].get("brief")
    assert brief is not None
    from jig.ticket import TicketStatus

    assert brief.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_po_finish_brief_on_empty_brief_raises(wired):
    wired["brief_path"].write_text("# myproj\n")  # no sections
    with pytest.raises(ValueError, match="empty"):
        await handle_po_finish_brief(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            summary="trying",
            author="po",
        )


@pytest.mark.asyncio
async def test_po_finish_brief_missing_file_raises_filenotfound(wired):
    wired["brief_path"].unlink()
    with pytest.raises(FileNotFoundError):
        await handle_po_finish_brief(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            summary="trying",
            author="po",
        )
