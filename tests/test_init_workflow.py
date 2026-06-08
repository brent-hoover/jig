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
    _create_planning_ticket,
    _scaffold_summary_for_pm,
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
from jig.project import Project, load_project, save_project
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
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "brief.md").write_text("# x\n")
    assert classify_directory(tmp_path) == DirState.IN_PROGRESS


def test_classify_partial_broken(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    # project.yaml missing AND there's some non-daemon-managed content
    # → genuinely inconsistent (init crashed mid-flow).
    (tmp_path / ".jig" / "spec").mkdir()
    assert classify_directory(tmp_path) == DirState.BROKEN


def test_classify_daemon_only_jig_is_fresh(tmp_path: Path):
    """When `jig` (no args) auto-spawns the daemon BEFORE init runs,
    the daemon creates .jig/run/ and .jig/logs/. classify_directory
    should treat that as FRESH so /init can proceed normally — not
    BROKEN as it would for a half-completed init."""
    (tmp_path / ".jig" / "run").mkdir(parents=True)
    (tmp_path / ".jig" / "logs").mkdir(parents=True)
    assert classify_directory(tmp_path) == DirState.FRESH


def test_classify_broken_invalid_yaml(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    (tmp_path / ".jig" / "project.yaml").write_text("not: : valid:\n  - yaml")
    assert classify_directory(tmp_path) == DirState.BROKEN


def test_create_stub(tmp_path: Path):
    target = tmp_path / "new"
    create_stub(target, name="new")
    assert (target / ".jig" / "project.yaml").is_file()
    assert (target / "docs" / "brief.md").is_file()
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


def test_create_stub_seeds_config_yaml_with_matching_id(tmp_path: Path):
    target = tmp_path / "p"
    create_stub(target, name="p")

    assert (target / ".jig" / "project.yaml").is_file()
    assert (target / ".jig" / "config.yaml").is_file()

    marker = yaml.safe_load((target / ".jig" / "project.yaml").read_text())
    project = load_project(target)

    assert marker["id"] == project.id
    assert project.name == "p"
    assert project.path == str(target.resolve())


def test_create_stub_idempotent_for_config_yaml(tmp_path: Path):
    target = tmp_path / "p"
    create_stub(target, name="p")
    first = load_project(target)
    create_stub(target, name="p")
    second = load_project(target)

    assert first.id == second.id
    assert first.path == second.path


def test_create_stub_default_brief_content(tmp_path: Path):
    target = tmp_path / "myproj"
    create_stub(target, name="myproj")
    brief = (target / "docs" / "brief.md").read_text()
    assert brief.startswith("# myproj")


async def _bootstrap_init_project(
    tmp_path: Path,
    *,
    roles: tuple[str, ...] = ("po",),
):
    """Create stub + role yamls + stores for a PO spawn."""
    create_stub(tmp_path, name="p")
    (tmp_path / ".jig" / "roles").mkdir(parents=True, exist_ok=True)
    # Reuse create_stub's generated id; only override non-default fields.
    project_id = load_project(tmp_path).id
    save_project(
        tmp_path,
        Project(
            id=project_id,
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
            payload={
                "gaps": [
                    {
                        "kind": "missing",
                        "severity": "blocking",
                        "location": "x",
                        "description": "d1",
                    }
                ]
            },
        )
    )
    await threads.post(
        Note(
            ticket_id="brief",
            author="spec-generator",
            text="second",
            payload={
                "gaps": [
                    {
                        "kind": "ambiguity",
                        "severity": "blocking",
                        "location": "y",
                        "description": "d2",
                    }
                ]
            },
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


def test_render_branch_prompt_returns_brief_context():
    """render_branch_prompt no longer enumerates choices — those are
    rendered as button-style badges by the TUI prompt panel. The
    function emits only the informational context line so the same
    info isn't duplicated in scrollback."""
    text = render_branch_prompt()
    assert "Brief accepted" in text
    # Options are NOT in the rendered text anymore.
    assert "[Y]" not in text
    assert "[p]" not in text
    assert "[s]" not in text


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
    # Options (Y/n/swap) come from the prompt panel — the rendered
    # text no longer duplicates them.
    assert "[Y/n/swap]" not in text


async def test_latest_scaffold_proposal_returns_most_recent(tmp_path: Path):
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    await threads.post(
        Note(
            ticket_id="architecture",
            author="sa",
            text="first",
            payload={
                "kind": "sa_propose_scaffold",
                "template_name": "python",
                "rationale": "simple",
                "config": {},
            },
        )
    )
    await threads.post(
        Note(
            ticket_id="architecture",
            author="sa",
            text="second",
            payload={
                "kind": "sa_propose_scaffold",
                "template_name": "fastapi",
                "rationale": "async",
                "config": {},
            },
        )
    )
    proposal = await latest_scaffold_proposal(threads)
    assert proposal is not None
    assert proposal["template_name"] == "fastapi"


async def test_run_sa_conversation_creates_arch_ticket_and_spawns(
    tmp_path: Path, monkeypatch
):
    tickets, threads, memory, bus = await _bootstrap_init_project(
        tmp_path,
        roles=("sa",),
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
    arch_dir.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.safe_dump(
        {
            "rationale": "fastapi is a good fit",
            "data_stores": [{"type": "postgres", "purpose": "primary"}],
        }
    )
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

    data = yaml.safe_load((project / ".jig" / "spec" / "architecture.yaml").read_text())
    assert data["rationale"] == "fastapi is a good fit"
    assert data["sa_path"] is True
    assert data["template"] == "fastapi"
    assert data["language"] == "python"
    assert data["framework"] == "fastapi"
    assert data["decisions"] == {"port": 8000}
    assert data["data_stores"][0]["type"] == "postgres"


async def _apply_scaffold_min(project, *, sa_path, tech_decisions=None, size="S"):
    """Run apply_scaffold against a stub project, returning architecture.yaml."""
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    await tickets.load()
    await threads.load()
    if sa_path:
        await tickets.create(
            Ticket(
                id="architecture",
                work_type=WorkType.ARCHITECTURE,
                title="Architecture",
                created_by="cli",
            )
        )
    else:
        await create_sa_skipped_marker(tickets=tickets, threads=threads)
    with patch("jig.init_workflow._apply_template_files"):
        await apply_scaffold(
            project_path=project,
            template_name="python",
            sa_path=sa_path,
            config=None,
            tech_decisions=tech_decisions,
            size=size,
            tickets=tickets,
            threads=threads,
        )
    return yaml.safe_load(
        (project / ".jig" / "spec" / "architecture.yaml").read_text()
    )


async def test_apply_scaffold_writes_tech_decisions_and_size(tmp_path: Path):
    project = tmp_path / "p"
    create_stub(project, name="p")
    tds = [
        {
            "id": "cli-framework",
            "choice": "typer",
            "rationale": "declarative",
            "source_type": "context7",
            "source_ref": "/typer/latest",
            "version_pinned": "0.12",
        }
    ]
    data = await _apply_scaffold_min(project, sa_path=True, tech_decisions=tds, size="M")
    assert data["tech_decisions"] == tds
    assert data["size"] == "M"


async def test_apply_scaffold_writes_size_without_tech_decisions(tmp_path: Path):
    """size is written unconditionally on the SA path so Phase 2 can read it;
    tech_decisions key is omitted when empty."""
    project = tmp_path / "p"
    create_stub(project, name="p")
    data = await _apply_scaffold_min(project, sa_path=True, tech_decisions=[], size="S")
    assert data["size"] == "S"
    assert "tech_decisions" not in data


async def test_apply_scaffold_direct_path_writes_neither(tmp_path: Path):
    project = tmp_path / "p"
    create_stub(project, name="p")
    data = await _apply_scaffold_min(project, sa_path=False, tech_decisions=[], size="M")
    assert "size" not in data
    assert "tech_decisions" not in data


async def test_apply_scaffold_sa_accept_seam_from_proposal(tmp_path: Path):
    """End-to-end seam (steps 3->4): a sa_propose_scaffold Note payload read
    back via latest_scaffold_proposal and fed to apply_scaffold via the
    SA-accept call site lands tech_decisions + size in architecture.yaml."""
    from jig.init_mcp import handle_sa_propose_scaffold

    project = tmp_path / "p"
    create_stub(project, name="p")
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    bus = MessageBus(project / ".jig" / "store" / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    await tickets.create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="Architecture",
            created_by="cli",
        )
    )
    tds = [
        {
            "id": "http-client",
            "choice": "httpx",
            "rationale": "async/sync",
            "source_type": "context7",
            "source_ref": "/encode/httpx",
            "version_pinned": "0.27",
        }
    ]
    await handle_sa_propose_scaffold(
        tickets=tickets,
        threads=threads,
        bus=bus,
        template_name="python",
        rationale="cli tool",
        tech_decisions=tds,
        size="M",
        author="sa",
    )
    proposal = await latest_scaffold_proposal(threads)
    with patch("jig.init_workflow._apply_template_files"):
        await apply_scaffold(
            project_path=project,
            template_name=proposal["template_name"],
            sa_path=True,
            config=proposal.get("decisions", {}),
            tech_decisions=proposal.get("tech_decisions", []),
            size=proposal.get("size", "S"),
            tickets=tickets,
            threads=threads,
        )
    data = yaml.safe_load(
        (project / ".jig" / "spec" / "architecture.yaml").read_text()
    )
    assert data["tech_decisions"] == tds
    assert data["size"] == "M"


def _write_arch(project: Path, arch: dict) -> None:
    arch_dir = project / ".jig" / "spec"
    arch_dir.mkdir(parents=True, exist_ok=True)
    (arch_dir / "architecture.yaml").write_text(yaml.safe_dump(arch))


def test_scaffold_summary_grounded_decisions_no_warning(tmp_path: Path):
    project = tmp_path / "p"
    create_stub(project, name="p")
    _write_arch(
        project,
        {
            "template": "python",
            "tech_decisions": [
                {
                    "id": "cli-framework",
                    "choice": "typer",
                    "source_type": "context7",
                    "source_ref": "/typer/latest",
                }
            ],
        },
    )
    summary = _scaffold_summary_for_pm(project)
    assert "Grounded tech decisions" in summary
    assert "cli-framework" in summary
    assert "Ungrounded decisions" not in summary


def test_scaffold_summary_inferred_decision_warns(tmp_path: Path):
    project = tmp_path / "p"
    create_stub(project, name="p")
    _write_arch(
        project,
        {
            "template": "python",
            "tech_decisions": [
                {"id": "auth-api", "choice": "oauth", "source_type": "inferred"}
            ],
        },
    )
    summary = _scaffold_summary_for_pm(project)
    assert "Ungrounded decisions" in summary
    assert "auth-api" in summary


def test_scaffold_summary_coexists_legacy_and_tech_decisions(tmp_path: Path):
    project = tmp_path / "p"
    create_stub(project, name="p")
    _write_arch(
        project,
        {
            "template": "python",
            "decisions": {"async_io": True},
            "tech_decisions": [
                {
                    "id": "http-client",
                    "choice": "httpx",
                    "source_type": "context7",
                    "source_ref": "/encode/httpx",
                }
            ],
        },
    )
    summary = _scaffold_summary_for_pm(project)
    assert "Architecture decisions" in summary  # legacy section
    assert "async_io" in summary
    assert "Grounded tech decisions" in summary  # new section
    assert "http-client" in summary


def test_scaffold_summary_no_tech_decisions_no_error(tmp_path: Path):
    project = tmp_path / "p"
    create_stub(project, name="p")
    _write_arch(project, {"template": "python"})
    summary = _scaffold_summary_for_pm(project)
    assert "Grounded tech decisions" not in summary
    assert summary  # template section still rendered


async def test_create_planning_ticket_not_blocked_by_inferred(tmp_path: Path):
    """Soft gate: an inferred (ungrounded) tech decision warns in the ticket
    description but does not block planning-ticket creation."""
    project = tmp_path / "p"
    create_stub(project, name="p")
    _write_arch(
        project,
        {
            "template": "python",
            "tech_decisions": [
                {"id": "auth-api", "choice": "oauth", "source_type": "inferred"}
            ],
        },
    )
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    await tickets.load()
    await _create_planning_ticket(tickets, project)
    ticket = await tickets.get("planning")
    assert ticket is not None
    assert "Ungrounded decisions" in ticket.description


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
    assert "hook install skipped" in out
    assert "simulated failure" in out
    # Scaffold's happy path still completed:
    assert (project / ".jig" / "spec" / "architecture.yaml").is_file()


def test_print_summary_includes_path_when_target_not_cwd(capsys):
    """`jig story` defaults to --path . — when init was run with a
    subdirectory target, the suggested commands need --path to point
    at the actual project."""
    from jig.init_workflow import _print_summary

    _print_summary(Path("dogfood"), template_name="fastapi")
    out = capsys.readouterr().out
    assert "jig story brief --path dogfood" in out
    assert "jig story architecture --path dogfood" in out


def test_print_summary_omits_path_when_target_is_cwd(capsys):
    """When init was run in-place (`jig init .` style), no --path
    needed — the bare command works. The summary now renders through
    a Rich panel, so we check the captured stdout text rather than a
    specific newline placement."""
    from jig.init_workflow import _print_summary

    _print_summary(Path("."), template_name="fastapi")
    out = capsys.readouterr().out
    assert "jig story brief" in out
    assert "--path" not in out
