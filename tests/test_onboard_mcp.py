"""onboard_finish_scan MCP tool handler + scanner role foundation."""

import pytest

from jig.init_mcp import handle_onboard_finish_scan
from jig.persistence import load_role
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note
from jig.ticket import Ticket, TicketStatus, WorkType


@pytest.fixture
async def wired(tmp_path):
    onboard_dir = tmp_path / ".jig" / "onboard"
    onboard_dir.mkdir(parents=True)
    (onboard_dir / "observations.md").write_text("# Observations\n\nStructure.\n")
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    await tickets.create(
        Ticket(
            id="onboard-scan",
            work_type=WorkType.ONBOARD_SCAN,
            title="Scan codebase",
            created_by="cli",
        )
    )
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
    }


def test_worktype_onboard_scan_exists():
    assert WorkType.ONBOARD_SCAN.value == "onboard_scan"


def test_scanner_role_loads(tmp_path):
    cfg = load_role(tmp_path, "scanner")
    assert cfg.role == "scanner"
    assert "onboard_finish_scan" in cfg.allowed_tools
    assert "Write" in cfg.allowed_tools
    assert cfg.allowed_mcps == []
    assert cfg.strict_tools is True


async def test_finish_scan_posts_note_and_resolves(wired):
    await handle_onboard_finish_scan(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        author="scanner",
    )
    entries = await wired["threads"].for_ticket("onboard-scan")
    notes = [e for e in entries if isinstance(e, Note)]
    assert len(notes) == 1
    assert notes[0].payload == {"kind": "onboard_scan_done"}
    assert notes[0].text == "scan complete"
    ticket = await wired["tickets"].get("onboard-scan")
    assert ticket.status == TicketStatus.RESOLVED


async def test_finish_scan_without_observations_raises(wired):
    (wired["project_path"] / ".jig" / "onboard" / "observations.md").unlink()
    with pytest.raises(ValueError, match="observations.md"):
        await handle_onboard_finish_scan(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            author="scanner",
        )
    ticket = await wired["tickets"].get("onboard-scan")
    assert ticket.status == TicketStatus.OPEN


async def test_finish_scan_publishes_orchestrator_event(wired):
    await handle_onboard_finish_scan(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        author="scanner",
    )
    history = await wired["bus"].get_history("orchestrator")
    msgs = [m for m in history if m.payload.get("kind") == "onboard_scan_done"]
    assert len(msgs) == 1
    assert msgs[0].topic == "orchestrator"
