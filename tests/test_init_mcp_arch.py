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
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    (tmp_path / ".jig" / "spec" / "project.structured.yaml").write_text(
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
    v = await handle_spec_get_field(project_path=wired["project_path"], path="name")
    assert v == "myproj"


@pytest.mark.asyncio
async def test_spec_get_field_nested(wired):
    v = await handle_spec_get_field(
        project_path=wired["project_path"], path="capabilities.due-dates"
    )
    assert v == {}


@pytest.mark.asyncio
async def test_spec_get_field_missing_returns_none(wired):
    v = await handle_spec_get_field(project_path=wired["project_path"], path="nope")
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
    v = await handle_arch_get_field(project_path=wired["project_path"], path="language")
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
        tickets=wired["tickets"],
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
async def test_sa_propose_scaffold_unknown_template_lists_valid_names(wired):
    """SA used to thrash through the filesystem hunting for the template
    list when its first guess was wrong. The error now tells it what's
    actually installed so it can self-correct in one turn."""
    with pytest.raises(KeyError) as exc:
        await handle_sa_propose_scaffold(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            template_name="python-web",
            rationale="r",
            config={},
            author="sa",
        )
    msg = str(exc.value)
    assert "python-web" in msg
    # Real templates that ship with the package — the assertion stays
    # valid as long as at least one of them is present.
    assert "fastapi" in msg or "python" in msg
    assert "arch_list_templates" in msg


@pytest.mark.asyncio
async def test_arch_list_templates_returns_metadata():
    """SA calls this to discover names + metadata before proposing.
    Verifies each entry has the expected metadata keys and that at
    least one shipped template appears."""
    from jig.init_mcp import handle_arch_list_templates

    templates = await handle_arch_list_templates()
    assert templates, "expected at least one shipped template"
    names = {t["name"] for t in templates}
    assert names & {"fastapi", "python"}, f"expected shipped templates in {names}"
    for t in templates:
        assert {
            "name",
            "description",
            "language",
            "framework",
            "deploy_target",
        } <= t.keys()
        assert isinstance(t["name"], str)
        assert isinstance(t["language"], str)


@pytest.mark.asyncio
async def test_sa_propose_scaffold_empty_rationale_raises(wired):
    with pytest.raises(ValueError, match="rationale"):
        await handle_sa_propose_scaffold(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            template_name="fastapi",
            rationale="",
            config={},
            author="sa",
        )


async def _proposal_payload(wired):
    entries = await wired["threads"].for_ticket("architecture")
    notes = [
        e
        for e in entries
        if e.kind == "note" and (e.payload or {}).get("kind") == "sa_propose_scaffold"
    ]
    assert notes, "expected a sa_propose_scaffold note"
    return notes[-1].payload


@pytest.mark.asyncio
async def test_sa_propose_scaffold_stores_tech_decisions_and_size(wired):
    tech_decisions = [
        {
            "id": "cli-framework",
            "choice": "typer",
            "rationale": "declarative subcommands",
            "source_type": "context7",
            "source_ref": "/typer/latest",
            "version_pinned": "0.12",
        }
    ]
    await handle_sa_propose_scaffold(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        template_name="python",
        rationale="cli tool",
        tech_decisions=tech_decisions,
        size="M",
        author="sa",
    )
    payload = await _proposal_payload(wired)
    assert payload["tech_decisions"] == tech_decisions
    assert payload["size"] == "M"


@pytest.mark.asyncio
async def test_sa_propose_scaffold_invalid_tech_decision_raises(wired):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await handle_sa_propose_scaffold(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            template_name="python",
            rationale="cli tool",
            # missing required 'choice'
            tech_decisions=[{"id": "cli-framework", "source_type": "inferred"}],
            author="sa",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["context7", "live_fetch"])
async def test_sa_propose_scaffold_grounded_without_source_ref_raises(wired, source_type):
    """The cross-field invariant (context7/live_fetch require source_ref) must
    fire through the MCP handler, not only at the schema level."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await handle_sa_propose_scaffold(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            template_name="python",
            rationale="cli tool",
            tech_decisions=[
                {"id": "cli-framework", "choice": "typer", "rationale": "x",
                 "source_type": source_type},  # no source_ref
            ],
            author="sa",
        )


@pytest.mark.asyncio
async def test_sa_propose_scaffold_duplicate_tech_decision_ids_raise(wired):
    """Duplicate ids must be rejected at the handler boundary — otherwise they
    pass init and break every downstream Architecture.model_validate."""
    with pytest.raises(ValueError, match="duplicate id"):
        await handle_sa_propose_scaffold(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            template_name="python",
            rationale="cli tool",
            tech_decisions=[
                {"id": "cli-framework", "choice": "typer", "rationale": "x",
                 "source_type": "inferred"},
                {"id": "cli-framework", "choice": "click", "rationale": "y",
                 "source_type": "inferred"},
            ],
            author="sa",
        )


@pytest.mark.asyncio
async def test_sa_propose_scaffold_stores_canonical_form(wired):
    """Stored tech_decisions are the validated model_dump (normalised), not the
    raw LLM dict — partial dicts gain the schema's default keys."""
    await handle_sa_propose_scaffold(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        template_name="python",
        rationale="cli tool",
        # no source_ref / version_pinned supplied
        tech_decisions=[{"id": "x", "choice": "y", "rationale": "z",
                         "source_type": "inferred"}],
        author="sa",
    )
    payload = await _proposal_payload(wired)
    stored = payload["tech_decisions"][0]
    assert stored["source_ref"] is None  # canonical default present
    assert stored["version_pinned"] is None


@pytest.mark.asyncio
async def test_sa_propose_scaffold_omitting_tech_decisions_stores_empty(wired):
    await handle_sa_propose_scaffold(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        template_name="python",
        rationale="cli tool",
        author="sa",
    )
    payload = await _proposal_payload(wired)
    assert payload["tech_decisions"] == []
    assert payload["size"] == "S"  # default


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_size", ["L", "X", "m"])
async def test_sa_propose_scaffold_invalid_size_raises(wired, bad_size):
    with pytest.raises(ValueError, match="size must be"):
        await handle_sa_propose_scaffold(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            template_name="python",
            rationale="cli tool",
            size=bad_size,
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


@pytest.mark.asyncio
async def test_arch_set_field_descend_list_with_non_numeric_key_raises_valueerror(
    wired,
):
    # First, set up a list at "items".
    await handle_arch_set_field(
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="items.0",
        value="first",
        author="sa",
    )
    # Now try to descend the list with a non-numeric segment (non-terminal).
    with pytest.raises(ValueError, match="cannot descend"):
        await handle_arch_set_field(
            threads=wired["threads"],
            project_path=wired["project_path"],
            path="items.foo.bar",
            value="x",
            author="sa",
        )
