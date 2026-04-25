"""CLI init entry: directory state, stub creation, top-level dispatch."""
from pathlib import Path

from jig import init_workflow as iw_mod
from jig.agent import RunAgentResult
from jig.init_workflow import (
    BranchChoice,
    DirState,
    classify_directory,
    create_stub,
    latest_gap_note,
    render_branch_prompt,
    render_gap_prompt,
    run_po_conversation,
)
from jig.models import RoleConfig
from jig.persistence import save_role
from jig.project import Project, save_project
from jig.spec_generator import Gap
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note
from jig.ticket import Ticket, WorkType


def test_classify_fresh_parent_missing(tmp_path: Path):
    target = tmp_path / "new-project"
    assert classify_directory(target) == DirState.FRESH


def test_classify_fresh_parent_exists(tmp_path: Path):
    target = tmp_path / "fresh-dir"
    target.mkdir()
    assert classify_directory(target) == DirState.FRESH


def test_classify_already_scaffolded(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    (tmp_path / ".jig" / "project.yaml").write_text(
        "id: x\nname: x\ncreated_at: 2026-01-01T00:00:00Z\n"
        "template_name: python\ntemplate_applied_at: 2026-01-01T00:00:00Z\n"
    )
    assert classify_directory(tmp_path) == DirState.ALREADY_DONE


def test_classify_in_progress_brief_only(tmp_path: Path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    (tmp_path / ".jig" / "project.yaml").write_text(
        "id: x\nname: x\ncreated_at: 2026-01-01T00:00:00Z\n"
    )
    (tmp_path / ".jig" / "spec" / "project.md").write_text("# x\n")
    assert classify_directory(tmp_path) == DirState.IN_PROGRESS


def test_classify_partial_broken(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    # project.yaml missing; this is inconsistent.
    assert classify_directory(tmp_path) == DirState.BROKEN


def test_classify_broken_invalid_yaml(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    (tmp_path / ".jig" / "project.yaml").write_text("not: : valid:\n  - yaml")
    assert classify_directory(tmp_path) == DirState.BROKEN


def test_create_stub(tmp_path: Path):
    target = tmp_path / "new"
    create_stub(target, name="new")
    assert (target / ".jig" / "project.yaml").is_file()
    assert (target / ".jig" / "spec" / "project.md").is_file()
    import yaml
    data = yaml.safe_load((target / ".jig" / "project.yaml").read_text())
    assert data["name"] == "new"
    assert "id" in data
    assert "created_at" in data


def test_create_stub_idempotent_when_consistent(tmp_path: Path):
    target = tmp_path / "new"
    create_stub(target, name="new")
    import yaml
    first = yaml.safe_load((target / ".jig" / "project.yaml").read_text())
    create_stub(target, name="new")  # should not raise, must not overwrite
    second = yaml.safe_load((target / ".jig" / "project.yaml").read_text())
    assert first["id"] == second["id"]
    assert first["created_at"] == second["created_at"]


def test_create_stub_default_brief_content(tmp_path: Path):
    target = tmp_path / "myproj"
    create_stub(target, name="myproj")
    brief = (target / ".jig" / "spec" / "project.md").read_text()
    assert brief.startswith("# myproj")


async def _bootstrap_init_project(tmp_path: Path):
    """Create stub + role yamls + stores for a PO spawn."""
    create_stub(tmp_path, name="p")
    (tmp_path / ".jig" / "roles").mkdir(parents=True, exist_ok=True)
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
    save_role(tmp_path, RoleConfig(role="po", phase_prompt="po"))
    tickets = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    memory = MemoryStore(tmp_path / ".jig" / "store")
    bus = MessageBus(tmp_path / ".jig" / "store" / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    return tickets, threads, memory, bus


async def test_run_po_conversation_creates_brief_ticket_and_spawns(
    tmp_path: Path, monkeypatch
):
    tickets, threads, memory, bus = await _bootstrap_init_project(tmp_path)

    captured = {}

    async def fake_run_agent(ctx, emitter=None):
        captured["ctx"] = ctx
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(iw_mod, "run_agent", fake_run_agent)

    await run_po_conversation(
        project_path=tmp_path,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )

    brief = await tickets.get("brief")
    assert brief is not None
    assert brief.work_type == WorkType.BRIEF

    ctx = captured["ctx"]
    assert ctx.role == "po"
    assert ctx.ticket.id == "brief"
    assert ctx.worktree_path == tmp_path


async def test_run_po_conversation_is_idempotent_on_existing_brief(
    tmp_path: Path, monkeypatch
):
    tickets, threads, memory, bus = await _bootstrap_init_project(tmp_path)
    await tickets.create(
        Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="Pre-existing brief",
            created_by="cli",
        )
    )

    captured = {}

    async def fake_run_agent(ctx, emitter=None):
        captured["ctx"] = ctx
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(iw_mod, "run_agent", fake_run_agent)

    await run_po_conversation(
        project_path=tmp_path,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )

    ctx = captured["ctx"]
    assert ctx.ticket.id == "brief"
    assert ctx.ticket.title == "Pre-existing brief"

    persisted = await tickets.get("brief")
    assert persisted is not None
    assert persisted.title == "Pre-existing brief"


async def test_latest_gap_note_returns_most_recent(tmp_path: Path):
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    await threads.post(
        Note(
            ticket_id="brief",
            author="spec-generator",
            text="first",
            payload={"gaps": [{"kind": "missing", "severity": "blocking",
                               "location": "x", "description": "d1"}]},
        )
    )
    await threads.post(
        Note(
            ticket_id="brief",
            author="spec-generator",
            text="second",
            payload={"gaps": [{"kind": "ambiguity", "severity": "blocking",
                               "location": "y", "description": "d2"}]},
        )
    )
    note = await latest_gap_note(threads)
    assert note is not None
    assert note.text == "second"


def test_render_gap_prompt_formats_gaps():
    gaps = [
        Gap(kind="missing", location="Built", description="X", severity="blocking"),
        Gap(kind="ambiguity", location="Planned", description="Y", severity="blocking"),
    ]
    text = render_gap_prompt(gaps)
    assert "[R] Resume PO" in text
    assert "[Q] Quit" in text
    assert "X" in text
    assert "Y" in text


def test_render_branch_prompt_contains_choices():
    text = render_branch_prompt()
    assert "[Y]" in text and "SA" in text
    assert "[p]" in text and "template" in text.lower()
    assert "[s]" in text and "PO" in text


def test_branch_choice_parsing():
    assert BranchChoice.parse("") == BranchChoice.SA
    assert BranchChoice.parse("Y") == BranchChoice.SA
    assert BranchChoice.parse("y") == BranchChoice.SA
    assert BranchChoice.parse("p") == BranchChoice.DIRECT
    assert BranchChoice.parse("s") == BranchChoice.STAY
    assert BranchChoice.parse("garbage") == BranchChoice.SA
