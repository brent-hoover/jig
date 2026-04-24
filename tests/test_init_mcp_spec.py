"""spec_* MCP tool handlers."""
import pytest
import yaml

from jig.init_mcp import handle_spec_publish, handle_spec_report_gaps
from jig.spec_generator import Gap
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note, SystemEvent
from jig.ticket import Ticket, WorkType


@pytest.fixture
async def wired(tmp_path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    await tickets.create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
    }


@pytest.mark.asyncio
async def test_spec_publish_writes_file_and_emits_event(wired):
    yaml_str = "name: myproj\ncapabilities: {}\n"
    await handle_spec_publish(
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        yaml_content=yaml_str,
        advisory_notes=[],
        author="spec-generator",
    )
    spec_file = wired["project_path"] / ".jig" / "spec" / "project.structured.yaml"
    assert spec_file.is_file()
    assert yaml.safe_load(spec_file.read_text())["name"] == "myproj"
    entries = await wired["threads"].for_ticket("brief")
    events = [e for e in entries if isinstance(e, SystemEvent)]
    assert any(e.event_type == "spec_generated" for e in events)
    notes = [e for e in entries if isinstance(e, Note)]
    assert len(notes) == 0


@pytest.mark.asyncio
async def test_spec_publish_with_advisory_notes_posts_note(wired):
    await handle_spec_publish(
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        yaml_content="name: x\n",
        advisory_notes=["consider clarifying X"],
        author="spec-generator",
    )
    entries = await wired["threads"].for_ticket("brief")
    notes = [e for e in entries if isinstance(e, Note)]
    assert len(notes) == 1
    assert "consider clarifying X" in notes[0].text


@pytest.mark.asyncio
async def test_spec_report_gaps_posts_note_with_payload(wired):
    gaps = [
        Gap(
            kind="missing",
            location="Planned (committed)",
            description="No capabilities listed.",
            severity="blocking",
        ),
    ]
    await handle_spec_report_gaps(
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        gaps=gaps,
        author="spec-generator",
    )
    entries = await wired["threads"].for_ticket("brief")
    notes = [e for e in entries if isinstance(e, Note)]
    assert len(notes) == 1
    assert notes[0].payload["gaps"][0]["kind"] == "missing"
    events = [e for e in entries if isinstance(e, SystemEvent)]
    assert any(e.event_type == "spec_gaps_reported" for e in events)
    spec_file = wired["project_path"] / ".jig" / "spec" / "project.structured.yaml"
    assert not spec_file.exists()


@pytest.mark.asyncio
async def test_spec_publish_rejects_unparseable_yaml(wired):
    with pytest.raises(ValueError, match="parse"):
        await handle_spec_publish(
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            yaml_content="::: not yaml :::",
            advisory_notes=[],
            author="spec-generator",
        )
    # Atomicity: spec file must NOT exist after a failed publish.
    spec_file = wired["project_path"] / ".jig" / "spec" / "project.structured.yaml"
    assert not spec_file.exists()
    # Atomicity: no spec_generated SystemEvent on the brief thread.
    entries = await wired["threads"].for_ticket("brief")
    events = [e for e in entries if isinstance(e, SystemEvent)]
    assert not any(e.event_type == "spec_generated" for e in events)
    # Atomicity: no bus message published to the orchestrator topic.
    history = await wired["bus"].get_history("orchestrator")
    assert all(m.payload.get("kind") != "spec_generated" for m in history)
