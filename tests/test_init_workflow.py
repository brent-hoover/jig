"""CLI init entry: directory state, stub creation, top-level dispatch."""
import subprocess
from pathlib import Path
from unittest.mock import patch

import yaml

from jig import init_workflow as iw_mod
from jig.agent import RunAgentResult
from jig.init_workflow import (
    BranchChoice,
    ConfirmChoice,
    DirState,
    apply_scaffold,
    classify_directory,
    create_stub,
    create_sa_skipped_marker,
    latest_gap_note,
    latest_scaffold_proposal,
    render_branch_prompt,
    render_gap_prompt,
    render_sa_confirm_prompt,
    render_template_list,
    run_po_conversation,
    run_sa_conversation,
)
from jig.models import RoleConfig
from jig.persistence import save_role
from jig.project import Project, save_project
from jig.spec_generator import Gap
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note, SystemEvent
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


async def _bootstrap_init_project(
    tmp_path: Path,
    *,
    roles: tuple[str, ...] = ("po",),
):
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
    for role in roles:
        save_role(tmp_path, RoleConfig(role=role, phase_prompt=role))
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


def test_confirm_choice_parsing():
    assert ConfirmChoice.parse("") == ConfirmChoice.YES
    assert ConfirmChoice.parse("Y") == ConfirmChoice.YES
    assert ConfirmChoice.parse("n") == ConfirmChoice.NO
    assert ConfirmChoice.parse("swap") == ConfirmChoice.SWAP


def test_render_sa_confirm_prompt_shows_rationale():
    text = render_sa_confirm_prompt(
        template_name="fastapi",
        rationale="real-time API, async needs.",
    )
    assert "fastapi" in text
    assert "real-time API, async needs." in text
    assert "[Y/n/swap]" in text


async def test_latest_scaffold_proposal_returns_most_recent(tmp_path: Path):
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    await threads.post(Note(
        ticket_id="architecture", author="sa", text="first",
        payload={"kind": "sa_propose_scaffold", "template_name": "python",
                 "rationale": "simple", "config": {}},
    ))
    await threads.post(Note(
        ticket_id="architecture", author="sa", text="second",
        payload={"kind": "sa_propose_scaffold", "template_name": "fastapi",
                 "rationale": "async", "config": {}},
    ))
    proposal = await latest_scaffold_proposal(threads)
    assert proposal is not None
    assert proposal["template_name"] == "fastapi"


async def test_run_sa_conversation_creates_arch_ticket_and_spawns(
    tmp_path: Path, monkeypatch
):
    tickets, threads, memory, bus = await _bootstrap_init_project(
        tmp_path, roles=("sa",),
    )
    captured = {}

    async def fake_run_agent(ctx, emitter=None):
        captured["ctx"] = ctx
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(iw_mod, "run_agent", fake_run_agent)

    await run_sa_conversation(
        project_path=tmp_path,
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )

    arch = await tickets.get("architecture")
    assert arch is not None
    assert arch.work_type == WorkType.ARCHITECTURE
    assert captured["ctx"].role == "sa"
    assert captured["ctx"].ticket.id == "architecture"
    assert captured["ctx"].worktree_path == tmp_path


def test_render_template_list_shows_numbered_choices():
    text = render_template_list(["python", "fastapi"])
    assert "1)" in text
    assert "python" in text
    assert "2)" in text
    assert "fastapi" in text


async def test_create_sa_skipped_marker_creates_ticket_and_event(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await tickets.load()
    await threads.load()
    await create_sa_skipped_marker(tickets=tickets, threads=threads)
    arch = await tickets.get("architecture")
    assert arch is not None
    entries = await threads.for_ticket("architecture")
    events = [e for e in entries if isinstance(e, SystemEvent)]
    assert any(e.event_type == "sa_skipped" for e in events)


async def test_apply_scaffold_direct_path_writes_architecture_yaml(tmp_path: Path):
    create_stub(tmp_path / "p", name="p")
    project = tmp_path / "p"
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    await tickets.load()
    await threads.load()
    await create_sa_skipped_marker(tickets=tickets, threads=threads)

    with patch("jig.init_workflow._apply_template_files"):
        await apply_scaffold(
            project_path=project,
            template_name="python",
            sa_path=False,
            config=None,
            tickets=tickets,
            threads=threads,
        )

    arch_file = project / ".jig" / "spec" / "architecture.yaml"
    assert arch_file.is_file()
    data = yaml.safe_load(arch_file.read_text())
    assert data["template"] == "python"
    assert data["sa_path"] is False
    assert data["language"] == "python"
    assert "rationale" not in data

    project_yaml = yaml.safe_load((project / ".jig" / "project.yaml").read_text())
    assert project_yaml["template_name"] == "python"
    assert "template_applied_at" in project_yaml

    events = await threads.for_ticket("architecture")
    assert any(
        isinstance(e, SystemEvent) and e.event_type == "scaffold_applied"
        for e in events
    )


async def test_apply_scaffold_sa_path_preserves_sa_fields(tmp_path: Path):
    create_stub(tmp_path / "p", name="p")
    project = tmp_path / "p"
    arch_dir = project / ".jig" / "spec"
    yaml_text = yaml.safe_dump({
        "rationale": "fastapi is a good fit",
        "data_stores": [{"type": "postgres", "purpose": "primary"}],
    })
    (arch_dir / "architecture.yaml").write_text(yaml_text)

    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    await tickets.load()
    await threads.load()
    await tickets.create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="Architecture",
            created_by="cli",
        )
    )

    with patch("jig.init_workflow._apply_template_files"):
        await apply_scaffold(
            project_path=project,
            template_name="fastapi",
            sa_path=True,
            config={"port": 8000},
            tickets=tickets,
            threads=threads,
        )

    data = yaml.safe_load((arch_dir / "architecture.yaml").read_text())
    assert data["rationale"] == "fastapi is a good fit"
    assert data["sa_path"] is True
    assert data["template"] == "fastapi"
    assert data["language"] == "python"
    assert data["framework"] == "fastapi"
    assert data["config"] == {"port": 8000}
    assert data["data_stores"][0]["type"] == "postgres"


async def test_apply_scaffold_installs_hooks_by_default(tmp_path: Path):
    """Hooks are installed when the project is a git repo."""
    project = tmp_path / "p"
    create_stub(project, name="p")
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    await tickets.load()
    await threads.load()

    with patch("jig.init_workflow._apply_template_files"):
        await apply_scaffold(
            project_path=project,
            template_name="python",
            sa_path=False,
            config=None,
            tickets=tickets,
            threads=threads,
        )

    from jig.hooks import HOOK_NAMES, _is_jig_managed
    for name in HOOK_NAMES:
        assert _is_jig_managed(project / ".git" / "hooks" / name), (
            f"{name} not installed by default"
        )


async def test_apply_scaffold_warns_and_succeeds_when_hook_install_fails(
    tmp_path: Path, monkeypatch, capsys
):
    """Hook install failure is non-fatal — scaffold must still complete."""
    project = tmp_path / "p"
    create_stub(project, name="p")
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    await tickets.load()
    await threads.load()

    from jig.hooks import HookInstallError
    import jig.hooks as hooks_mod

    def boom(_path, *, force=False):
        raise HookInstallError("simulated failure")

    monkeypatch.setattr(hooks_mod, "install_hooks", boom)

    with patch("jig.init_workflow._apply_template_files"):
        await apply_scaffold(
            project_path=project,
            template_name="python",
            sa_path=False,
            config=None,
            tickets=tickets,
            threads=threads,
        )

    out = capsys.readouterr().out
    assert "Warning: hook install failed" in out
    assert "simulated failure" in out
    # Scaffold's happy path still completed:
    assert (project / ".jig" / "spec" / "architecture.yaml").is_file()
