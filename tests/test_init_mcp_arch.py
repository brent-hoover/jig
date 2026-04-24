"""spec_get_field / arch_* / sa_propose_scaffold MCP tool handlers."""
import pytest
import yaml

from jig.init_mcp import (
    handle_arch_get_field,
    handle_arch_list_fields,
    handle_arch_set_field,
    handle_sa_propose_scaffold,
    handle_spec_get_field,
    handle_spec_list_fields,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note
from jig.ticket import Ticket, WorkType


@pytest.fixture
async def wired(tmp_path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.structured.yaml").write_text(
        yaml.safe_dump({"name": "myproj", "capabilities": {"due-dates": {}}})
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    await tickets.create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="Arch",
            created_by="cli",
        )
    )
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
    }


@pytest.mark.asyncio
async def test_spec_get_field_returns_value(wired):
    v = await handle_spec_get_field(
        project_path=wired["project_path"], path="name"
    )
    assert v == "myproj"


@pytest.mark.asyncio
async def test_spec_get_field_nested(wired):
    v = await handle_spec_get_field(
        project_path=wired["project_path"], path="capabilities.due-dates"
    )
    assert v == {}


@pytest.mark.asyncio
async def test_spec_get_field_missing_returns_none(wired):
    v = await handle_spec_get_field(
        project_path=wired["project_path"], path="nope"
    )
    assert v is None


@pytest.mark.asyncio
async def test_spec_list_fields_recursive(wired):
    fields = await handle_spec_list_fields(project_path=wired["project_path"])
    assert "name" in fields
    assert "capabilities" in fields


@pytest.mark.asyncio
async def test_arch_set_field_creates_file(wired):
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="rationale",
        value="because I said so",
        author="sa",
    )
    arch_file = wired["project_path"] / ".jig" / "spec" / "architecture.yaml"
    assert arch_file.is_file()
    data = yaml.safe_load(arch_file.read_text())
    assert data["rationale"] == "because I said so"


@pytest.mark.asyncio
async def test_arch_set_field_nested(wired):
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="data_stores.0",
        value={"type": "postgres", "purpose": "primary"},
        author="sa",
    )
    arch_file = wired["project_path"] / ".jig" / "spec" / "architecture.yaml"
    data = yaml.safe_load(arch_file.read_text())
    assert data["data_stores"][0]["type"] == "postgres"


@pytest.mark.asyncio
async def test_arch_get_field_reads_back(wired):
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="language",
        value="python",
        author="sa",
    )
    v = await handle_arch_get_field(
        project_path=wired["project_path"], path="language"
    )
    assert v == "python"


@pytest.mark.asyncio
async def test_arch_list_fields_returns_keys(wired):
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="language",
        value="python",
        author="sa",
    )
    fields = await handle_arch_list_fields(project_path=wired["project_path"])
    assert "language" in fields


@pytest.mark.asyncio
async def test_sa_propose_scaffold_records_proposal(wired):
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="rationale",
        value="spec implies async backend",
        author="sa",
    )
    await handle_sa_propose_scaffold(
        threads=wired["threads"],
        bus=wired["bus"],
        template_name="fastapi",
        rationale="spec implies async backend",
        config={},
        author="sa",
    )
    entries = await wired["threads"].for_ticket("architecture")
    notes = [e for e in entries if e.kind == "note"]
    assert any("fastapi" in n.text for n in notes)


@pytest.mark.asyncio
async def test_sa_propose_scaffold_unknown_template_raises(wired):
    with pytest.raises(KeyError):
        await handle_sa_propose_scaffold(
            threads=wired["threads"],
            bus=wired["bus"],
            template_name="does-not-exist",
            rationale="r",
            config={},
            author="sa",
        )


@pytest.mark.asyncio
async def test_sa_propose_scaffold_empty_rationale_raises(wired):
    with pytest.raises(ValueError, match="rationale"):
        await handle_sa_propose_scaffold(
            threads=wired["threads"],
            bus=wired["bus"],
            template_name="fastapi",
            rationale="",
            config={},
            author="sa",
        )


@pytest.mark.asyncio
async def test_arch_set_field_three_segment_path_with_list_index(wired):
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="data_stores.0.type",
        value="postgres",
        author="sa",
    )
    arch_file = wired["project_path"] / ".jig" / "spec" / "architecture.yaml"
    data = yaml.safe_load(arch_file.read_text())
    assert data == {"data_stores": [{"type": "postgres"}]}


@pytest.mark.asyncio
async def test_arch_set_field_disjoint_writes_both_persist(wired):
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="rationale",
        value="async backend",
        author="sa",
    )
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="language",
        value="python",
        author="sa",
    )
    arch_file = wired["project_path"] / ".jig" / "spec" / "architecture.yaml"
    data = yaml.safe_load(arch_file.read_text())
    assert data["rationale"] == "async backend"
    assert data["language"] == "python"


@pytest.mark.asyncio
async def test_arch_set_field_posts_tool_use_note(wired):
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="rationale",
        value="async backend",
        author="sa",
    )
    entries = await wired["threads"].for_ticket("architecture")
    notes = [e for e in entries if isinstance(e, Note)]
    assert len(notes) == 1
    assert notes[0].payload == {"path": "rationale", "value": "async backend"}
    assert notes[0].author == "sa"
