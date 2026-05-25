from pathlib import Path

import pytest

from jig.models import RoleConfig
from jig.store import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note
from jig.ticket import TicketStatus, WorkType
from jig.ticket_mcp import (
    handle_comment_on_ticket,
    handle_create_ticket,
    handle_list_tickets,
    handle_read_comments,
    handle_read_ticket,
    handle_update_ticket,
)
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.fixture
async def stores(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    return tickets, threads, bus


@pytest.mark.asyncio
async def test_create_ticket_persists(stores) -> None:
    tickets, threads, bus = stores
    ticket_id = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="user",
        args={
            "type": "feature",
            "title": "Add search",
            "description": "users want to search\n\n" + TICKET_AC_PLACEHOLDER,
        },
    )
    loaded = await tickets.get(ticket_id)
    assert loaded is not None
    assert loaded.title == "Add search"
    assert loaded.work_type == WorkType.FEATURE
    assert loaded.created_by == "user"
    assert loaded.status == TicketStatus.OPEN


@pytest.mark.asyncio
async def test_create_ticket_publishes_bus_event(stores) -> None:
    tickets, threads, bus = stores
    queue = await bus.subscribe("orchestrator")
    await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="user",
        args={"type": "bug", "title": "crash", "description": TICKET_AC_PLACEHOLDER},
    )
    msg = await queue.get()
    assert msg.topic == "orchestrator"
    assert msg.payload["kind"] == "ticket_created"
    # Legacy "bug" migrates to "bugfix" via the Ticket model validator.
    assert msg.payload["work_type"] == "bugfix"


@pytest.mark.asyncio
async def test_create_ticket_with_wired_callback_broadcasts_once(stores) -> None:
    """Pins the double-publish prevention contract.

    Production stores have ``wire_create_publisher`` registered (it
    fires the broadcast topic from the store callback). When
    ``handle_create_ticket`` runs against such a store it MUST
    publish broadcast exactly once — relying on
    ``fire_create_callback=False`` to suppress the store callback
    for that single create while it publishes both topics itself.

    A refactor that drops the ``fire_create_callback=False`` flag,
    or moves ``wire_create_publisher`` inside the handler, would
    silently regress this invariant. Without this test nothing
    catches that.
    """
    from jig.ticket_events import wire_create_publisher

    tickets, _, bus = stores
    wire_create_publisher(tickets, bus, sender="store")

    ticket_id = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="pm",
        args={
            "type": "feature",
            "title": "broadcast invariant",
            "description": TICKET_AC_PLACEHOLDER,
        },
    )
    # Drain the wire-up's background task (no-op if the suppression
    # worked — there's no task to await — but covers the regression
    # case where a leaked callback fired).
    await tickets.drain_background_tasks()

    # Exactly one broadcast publish (the handler's own), not two
    # (handler + un-suppressed callback).
    broadcast_history = await bus.get_history(f"tickets.{ticket_id}", limit=10)
    creates = [
        m for m in broadcast_history if m.payload.get("kind") == "ticket_created"
    ]
    assert len(creates) == 1, (
        f"expected exactly one ticket_created broadcast on tickets.{ticket_id}, "
        f"got {len(creates)} — store callback double-published"
    )

    # And exactly one orchestrator-topic publish for dispatch.
    orch_history = await bus.get_history("orchestrator", limit=10)
    orch_creates = [
        m
        for m in orch_history
        if m.payload.get("kind") == "ticket_created"
        and m.payload.get("ticket_id") == ticket_id
    ]
    assert len(orch_creates) == 1


@pytest.mark.asyncio
async def test_create_ticket_rejects_unknown_work_type(stores) -> None:
    tickets, threads, bus = stores
    with pytest.raises(ValueError, match=r"Unknown work_type 'gizmo'.*feature"):
        await handle_create_ticket(
            tickets=tickets,
            bus=bus,
            sender="user",
            args={"work_type": "gizmo", "title": "bad"},
        )


@pytest.mark.asyncio
async def test_create_ticket_rejects_unknown_legacy_type(stores) -> None:
    tickets, threads, bus = stores
    with pytest.raises(ValueError, match=r"Unknown work_type 'widget'"):
        await handle_create_ticket(
            tickets=tickets,
            bus=bus,
            sender="user",
            args={"type": "widget", "title": "bad"},
        )


@pytest.mark.asyncio
async def test_create_ticket_rejects_unknown_size(stores) -> None:
    tickets, threads, bus = stores
    with pytest.raises(ValueError, match=r"Unknown size 'huge'"):
        await handle_create_ticket(
            tickets=tickets,
            bus=bus,
            sender="user",
            args={
                "work_type": "feature",
                "title": "bad",
                "size": "huge",
                "description": TICKET_AC_PLACEHOLDER,
            },
        )


@pytest.mark.asyncio
async def test_read_ticket(stores) -> None:
    tickets, threads, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="user",
        args={"type": "feature", "title": "f", "description": TICKET_AC_PLACEHOLDER},
    )
    loaded = await handle_read_ticket(tickets=tickets, ticket_id=tid)
    assert loaded.title == "f"


@pytest.mark.asyncio
async def test_read_ticket_missing_raises(stores) -> None:
    tickets, _, _ = stores
    with pytest.raises(KeyError):
        await handle_read_ticket(tickets=tickets, ticket_id="nope")


@pytest.mark.asyncio
async def test_list_tickets_filtered(stores) -> None:
    tickets, threads, bus = stores
    await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={"type": "feature", "title": "f1", "description": TICKET_AC_PLACEHOLDER},
    )
    await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={"type": "bug", "title": "b1", "description": TICKET_AC_PLACEHOLDER},
    )
    features = await handle_list_tickets(
        tickets=tickets, args={"type": "feature", "description": TICKET_AC_PLACEHOLDER}
    )
    assert [t.title for t in features] == ["f1"]


@pytest.mark.asyncio
async def test_read_comments_direct_post(stores) -> None:
    tickets, threads, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={"type": "task", "title": "t", "description": TICKET_AC_PLACEHOLDER},
    )
    await threads.post(Note(ticket_id=tid, author="dev", text="hello"))
    entries = await handle_read_comments(threads=threads, ticket_id=tid)
    # filter to note kind (there may also be SystemEvents)
    notes = [e for e in entries if e.kind == "note"]
    assert [n.text for n in notes] == ["hello"]


@pytest.mark.asyncio
async def test_comment_on_ticket_rejects_system_kinds(stores) -> None:
    tickets, threads, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={"type": "task", "title": "t", "description": TICKET_AC_PLACEHOLDER},
    )
    with pytest.raises(ValueError):
        await handle_comment_on_ticket(
            tickets=tickets,
            threads=threads,
            bus=bus,
            sender="dev",
            sender_cfg=None,
            args={"ticket_id": tid, "content": "x", "kind": "phase_run"},
        )


@pytest.mark.asyncio
async def test_update_ticket_status_emits_status_change_comment(stores) -> None:
    tickets, threads, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={"type": "feature", "title": "f", "description": TICKET_AC_PLACEHOLDER},
    )
    await handle_update_ticket(
        tickets=tickets,
        threads=threads,
        bus=bus,
        sender="orchestrator",
        args={"ticket_id": tid, "status": "in_progress"},
    )
    entries = await threads.for_ticket(tid)
    status_changes = [
        e
        for e in entries
        if e.kind == "system_event" and e.event_type == "status_change"
    ]
    assert len(status_changes) == 1
    assert "in_progress" in status_changes[0].content


@pytest.mark.asyncio
async def test_update_ticket_non_status_field(stores) -> None:
    tickets, threads, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={"type": "feature", "title": "f", "description": TICKET_AC_PLACEHOLDER},
    )
    new_description = "more detail\n\n" + TICKET_AC_PLACEHOLDER
    await handle_update_ticket(
        tickets=tickets,
        threads=threads,
        bus=bus,
        sender="orchestrator",
        args={"ticket_id": tid, "description": new_description},
    )
    loaded = await tickets.get(tid)
    assert loaded.description == new_description
    entries = await threads.for_ticket(tid)
    assert not any(
        e.kind == "system_event" and e.event_type == "status_change" for e in entries
    )


@pytest.mark.asyncio
async def test_comment_on_ticket_self_role_allowed(stores) -> None:
    """A dev agent can comment on a dev-assigned ticket."""
    tickets, threads, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={
            "type": "task",
            "title": "t",
            "assignee": "dev",
            "description": TICKET_AC_PLACEHOLDER,
        },
    )
    dev_cfg = RoleConfig(role="dev", phase_prompt="")
    cid = await handle_comment_on_ticket(
        tickets=tickets,
        threads=threads,
        bus=bus,
        sender="dev",
        sender_cfg=dev_cfg,
        args={"ticket_id": tid, "content": "progress"},
    )
    assert cid


@pytest.mark.asyncio
async def test_comment_on_ticket_orchestrator_always_reachable(stores) -> None:
    """Any agent can still reach the orchestrator."""
    tickets, threads, bus = stores
    tid = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={
            "type": "task",
            "title": "t",
            "assignee": "orchestrator",
            "description": TICKET_AC_PLACEHOLDER,
        },
    )
    dev_cfg = RoleConfig(role="dev", phase_prompt="")
    cid = await handle_comment_on_ticket(
        tickets=tickets,
        threads=threads,
        bus=bus,
        sender="dev",
        sender_cfg=dev_cfg,
        args={"ticket_id": tid, "content": "question for orchestrator"},
    )
    assert cid


@pytest.mark.asyncio
async def test_commit_progress_creates_commit_and_system_event(
    stores, tmp_path
) -> None:
    import subprocess

    tickets, threads, bus = stores

    work = tmp_path / "worktree"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    (work / "a.txt").write_text("hello")

    tid = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={"type": "feature", "title": "f", "description": TICKET_AC_PLACEHOLDER},
    )
    from jig.ticket_mcp import handle_commit_progress

    result = await handle_commit_progress(
        tickets=tickets,
        threads=threads,
        bus=bus,
        sender="dev",
        worktree_path=work,
        args={"ticket_id": tid, "message": "add a.txt"},
    )
    assert "sha" in result
    assert result["sha"]
    entries = await threads.for_ticket(tid)
    commits = [
        e for e in entries if e.kind == "system_event" and e.event_type == "commit"
    ]
    assert len(commits) == 1
    assert commits[0].commit_sha == result["sha"]
    assert commits[0].content == "feat(dev): add a.txt"


@pytest.mark.asyncio
async def test_commit_progress_nothing_to_commit_returns_none_sha(
    stores, tmp_path
) -> None:
    import subprocess

    tickets, threads, bus = stores
    work = tmp_path / "worktree2"
    work.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=work, check=True
    )

    tid = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="u",
        args={"type": "feature", "title": "f", "description": TICKET_AC_PLACEHOLDER},
    )
    from jig.ticket_mcp import handle_commit_progress

    result = await handle_commit_progress(
        tickets=tickets,
        threads=threads,
        bus=bus,
        sender="dev",
        worktree_path=work,
        args={"ticket_id": tid, "message": "noop"},
    )
    assert result["sha"] is None
    entries = await threads.for_ticket(tid)
    commits = [
        e for e in entries if e.kind == "system_event" and e.event_type == "commit"
    ]
    assert commits == []


@pytest.mark.asyncio
async def test_record_learning_writes_to_memory_store(tmp_path: Path) -> None:
    from jig.store.memory import MemoryStore
    from jig.ticket_mcp import handle_record_learning

    memory = MemoryStore(tmp_path)
    await memory.load()
    await handle_record_learning(
        memory=memory,
        role="dev",
        args={"content": "always use uv run"},
    )
    learnings = await memory.get_role_learnings("dev")
    assert [learning.content for learning in learnings] == ["always use uv run"]


@pytest.mark.asyncio
async def test_record_learning_fans_out_to_multiple_roles(tmp_path: Path) -> None:
    from jig.store.memory import MemoryStore
    from jig.ticket_mcp import handle_record_learning

    memory = MemoryStore(tmp_path)
    await memory.load()
    await handle_record_learning(
        memory=memory,
        role="dev",
        args={"content": "use disable_error_codes not ignore_errors", "roles": ["dev", "test"]},
    )
    dev = await memory.get_role_learnings("dev")
    test = await memory.get_role_learnings("test")
    assert [l.content for l in dev] == ["use disable_error_codes not ignore_errors"]
    assert [l.content for l in test] == ["use disable_error_codes not ignore_errors"]


@pytest.mark.asyncio
async def test_record_learning_defaults_to_calling_role(tmp_path: Path) -> None:
    from jig.store.memory import MemoryStore
    from jig.ticket_mcp import handle_record_learning

    memory = MemoryStore(tmp_path)
    await memory.load()
    await handle_record_learning(
        memory=memory,
        role="review",
        args={"content": "tip"},
    )
    assert len(await memory.get_role_learnings("review")) == 1
    assert len(await memory.get_role_learnings("dev")) == 0


@pytest.mark.asyncio
async def test_request_context_reads_worktree_file(tmp_path: Path) -> None:
    from jig.ticket_mcp import handle_request_context

    work = tmp_path / "w"
    work.mkdir()
    (work / "README.md").write_text("hello")
    result = await handle_request_context(
        worktree_path=work,
        args={"path": "README.md"},
    )
    assert result == "hello"


@pytest.mark.asyncio
async def test_request_context_missing_file(tmp_path: Path) -> None:
    from jig.ticket_mcp import handle_request_context

    work = tmp_path / "w"
    work.mkdir()
    result = await handle_request_context(
        worktree_path=work,
        args={"path": "nope.txt"},
    )
    assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# Path traversal containment — Block 1 critical 1.
# An agent that supplies "../" or absolute or symlink-escaping paths
# must not be able to read host files outside the worktree.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_context_rejects_dotdot_traversal(tmp_path: Path) -> None:
    from jig.ticket_mcp import handle_request_context

    work = tmp_path / "w"
    work.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("PWNED")

    result = await handle_request_context(
        worktree_path=work,
        args={"path": "../secret.txt"},
    )
    assert "PWNED" not in result
    assert "Invalid path" in result


@pytest.mark.asyncio
async def test_request_context_rejects_absolute_path(tmp_path: Path) -> None:
    from jig.ticket_mcp import handle_request_context

    work = tmp_path / "w"
    work.mkdir()

    result = await handle_request_context(
        worktree_path=work,
        args={"path": "/etc/passwd"},
    )
    assert "Invalid path" in result


@pytest.mark.asyncio
async def test_request_context_rejects_symlink_escape(tmp_path: Path) -> None:
    from jig.ticket_mcp import handle_request_context

    work = tmp_path / "w"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("LEAKED")
    # Symlink lives inside the worktree but points outside — the
    # per-segment validator can't see this, only the resolve()
    # containment check catches it.
    (work / "escape").symlink_to(outside)

    result = await handle_request_context(
        worktree_path=work,
        args={"path": "escape/secret.txt"},
    )
    assert "LEAKED" not in result
    assert "Invalid path" in result


@pytest.mark.asyncio
async def test_request_context_rejects_hidden_file(tmp_path: Path) -> None:
    from jig.ticket_mcp import handle_request_context

    work = tmp_path / "w"
    work.mkdir()
    (work / ".env").write_text("SECRET=123")

    result = await handle_request_context(
        worktree_path=work,
        args={"path": ".env"},
    )
    assert "SECRET" not in result
    assert "Invalid path" in result


@pytest.mark.asyncio
async def test_request_context_allows_uppercase_filename(tmp_path: Path) -> None:
    """Real source trees have README.md / Cargo.toml — must not block these."""
    from jig.ticket_mcp import handle_request_context

    work = tmp_path / "w"
    work.mkdir()
    (work / "README.md").write_text("ok")

    result = await handle_request_context(
        worktree_path=work,
        args={"path": "README.md"},
    )
    assert result == "ok"


# ---- spec materialisation from project spec (Phase 3 follow-on) -----------


def _write_project_spec(project_path: Path, cap_id: str) -> None:
    """Write a minimal project.structured.yaml with one capability."""
    spec_dir = project_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "project.structured.yaml").write_text(
        f"""name: test-project
summary: A small thing
spec_version: 1
generated_at: '2026-05-24T00:00:00Z'
capabilities:
  - id: {cap_id}
    title: Fetch stories
    state: planned
    summary: Pull top HN stories.
    behaviors:
      - id: run-top
        description: "`hn-cli top` prints N stories"
        acceptance_criteria:
          - Exit code is 0 on success.
          - Output has N lines.
    acceptance_criteria: []
    examples: []
    done_enough: []
    excluded:
      - pagination
    open_questions: []
    tickets: []
    aliases: []
    created_at: '2026-05-24T00:00:00Z'
    last_updated: '2026-05-24T00:00:00Z'
    state_changed_at: '2026-05-24T00:00:00Z'
non_goals: []
"""
    )


def _initialised_project(tmp_path: Path) -> Path:
    """tmp project ready for spec save (has .git + init_project applied)."""
    from jig.persistence import init_project

    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git").mkdir()
    init_project(tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_create_ticket_materialises_spec_from_capability(
    stores, tmp_path: Path
) -> None:
    """When derived_from points at a project-spec capability, the ticket
    spec is written inline as part of handle_create_ticket."""
    from jig.specs import load_ticket_spec

    tickets, _threads, bus = stores
    project = _initialised_project(tmp_path / "proj")
    _write_project_spec(project, "fetch-top")

    ticket_id = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="pm",
        args={
            "work_type": "feature",
            "title": "Fetch top stories",
            "description": "stuff\n\n" + TICKET_AC_PLACEHOLDER,
            "derived_from": "project://spec/capabilities/fetch-top",
            "size": "m",
        },
        project_path=project,
    )

    spec = load_ticket_spec(project, ticket_id)
    assert spec is not None
    assert spec.fields["summary"] == "Pull top HN stories."
    assert spec.fields["acceptance_criteria"] == [
        "Exit code is 0 on success.",
        "Output has N lines.",
    ]
    assert spec.fields["out_of_scope"] == ["pagination"]
    assert len(spec.fields["behaviors"]) == 1


@pytest.mark.asyncio
async def test_create_ticket_without_derived_from_writes_no_spec(
    stores, tmp_path: Path
) -> None:
    """Tickets created without derived_from leave .jig/specs/ untouched."""
    from jig.specs import load_ticket_spec

    tickets, _threads, bus = stores
    project = _initialised_project(tmp_path / "proj")
    _write_project_spec(project, "fetch-top")

    ticket_id = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="pm",
        args={
            "work_type": "feature",
            "title": "Some ticket",
            "description": "x\n\n" + TICKET_AC_PLACEHOLDER,
        },
        project_path=project,
    )
    assert load_ticket_spec(project, ticket_id) is None


@pytest.mark.asyncio
async def test_create_ticket_with_unknown_capability_logs_warning(
    stores, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Bogus derived_from URI logs a warning but ticket is still created."""
    import logging

    from jig.specs import load_ticket_spec

    tickets, _threads, bus = stores
    project = _initialised_project(tmp_path / "proj")
    _write_project_spec(project, "fetch-top")

    with caplog.at_level(logging.WARNING, logger="jig.ticket_mcp"):
        ticket_id = await handle_create_ticket(
            tickets=tickets,
            bus=bus,
            sender="pm",
            args={
                "work_type": "feature",
                "title": "Bogus",
                "description": "x\n\n" + TICKET_AC_PLACEHOLDER,
                "derived_from": "project://spec/capabilities/does-not-exist",
            },
            project_path=project,
        )

    assert await tickets.get(ticket_id) is not None
    assert load_ticket_spec(project, ticket_id) is None
    assert any("does-not-exist" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_create_ticket_materialises_spec_for_large_ticket(
    stores, tmp_path: Path
) -> None:
    """L/XL feature tickets get a spec from the capability even though
    the work-type schema would otherwise require ``design`` /
    ``technical_risks`` for that size — the materialiser writes with
    ``enforce_required_fields=False`` because the project-spec
    capability never carries those fields."""
    from jig.specs import load_ticket_spec

    tickets, _threads, bus = stores
    project = _initialised_project(tmp_path / "proj")
    _write_project_spec(project, "fetch-top")

    ticket_id = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="pm",
        args={
            "work_type": "feature",
            "title": "big ticket",
            "description": "x\n\n" + TICKET_AC_PLACEHOLDER,
            "derived_from": "project://spec/capabilities/fetch-top",
            "size": "l",
        },
        project_path=project,
    )
    spec = load_ticket_spec(project, ticket_id)
    assert spec is not None
    assert "acceptance_criteria" in spec.fields
    assert "design" not in spec.fields


@pytest.mark.asyncio
async def test_create_ticket_non_feature_with_derived_from_skips_spec(
    stores, tmp_path: Path
) -> None:
    """A bugfix ticket carrying ``derived_from`` is treated as a
    metadata-only link — no spec is materialised. The feature-shape
    fields the materialiser would otherwise emit are not in the bugfix
    work-type schema, so the guard prevents the materialiser from
    writing fields that don't belong on that ticket's spec."""
    from jig.specs import load_ticket_spec

    tickets, _threads, bus = stores
    project = _initialised_project(tmp_path / "proj")
    _write_project_spec(project, "fetch-top")

    ticket_id = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="pm",
        args={
            "work_type": "bugfix",
            "title": "fix bug",
            "description": "x\n\n" + TICKET_AC_PLACEHOLDER,
            "derived_from": "project://spec/capabilities/fetch-top",
        },
        project_path=project,
    )
    ticket = await tickets.get(ticket_id)
    assert ticket is not None
    # The link is preserved on the ticket record for traceability.
    assert ticket.derived_from == "project://spec/capabilities/fetch-top"
    # But no spec was materialised — bugfix can't accept feature-shape
    # fields.
    assert load_ticket_spec(project, ticket_id) is None


@pytest.mark.asyncio
async def test_create_ticket_with_malformed_project_spec_dispatches(
    stores, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A malformed project.structured.yaml must not orphan a ticket.
    Materialisation logs a warning and skips; dispatch still fires."""
    import logging

    from jig.specs import load_ticket_spec

    tickets, _threads, bus = stores
    project = _initialised_project(tmp_path / "proj")
    (project / ".jig" / "spec").mkdir(parents=True, exist_ok=True)
    # Garbage that yaml.safe_load can parse but pydantic rejects.
    (project / ".jig" / "spec" / "project.structured.yaml").write_text(
        "this: is\nnot: a structured spec\n"
    )

    queue = await bus.subscribe("orchestrator")
    with caplog.at_level(logging.WARNING, logger="jig.ticket_mcp"):
        ticket_id = await handle_create_ticket(
            tickets=tickets,
            bus=bus,
            sender="pm",
            args={
                "work_type": "feature",
                "title": "would-have-orphaned",
                "description": "x\n\n" + TICKET_AC_PLACEHOLDER,
                "derived_from": "project://spec/capabilities/anything",
            },
            project_path=project,
        )

    assert await tickets.get(ticket_id) is not None
    assert load_ticket_spec(project, ticket_id) is None
    # Dispatch event MUST have fired — the contract this test exists to
    # protect.
    msg = await queue.get()
    assert msg.payload["kind"] == "ticket_created"
    assert msg.payload["ticket_id"] == ticket_id
    # And the materialisation failure surfaced in the log.
    assert any("failed to load project spec" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_create_ticket_with_capability_lacking_ac_warns(
    stores, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A capability with no AC (neither top-level nor per-behavior)
    produces a spec with empty acceptance_criteria. The reviewer-test-
    adequacy gate would have nothing to check; surface that as a
    warning instead of silently shipping an ungated feature."""
    import logging

    tickets, _threads, bus = stores
    project = _initialised_project(tmp_path / "proj")
    # Project spec with one capability that has *no* AC at all. This
    # uses ``backlog`` state because PLANNED requires AC by schema.
    (project / ".jig" / "spec").mkdir(parents=True, exist_ok=True)
    (project / ".jig" / "spec" / "project.structured.yaml").write_text(
        """name: ac-less
summary: A capability with no AC
spec_version: 1
generated_at: '2026-05-24T00:00:00Z'
capabilities:
  - id: empty-cap
    title: Has no AC
    state: backlog
    summary: nothing to test
    behaviors: []
    acceptance_criteria: []
    examples: []
    done_enough: []
    excluded: []
    open_questions: []
    tickets: []
    aliases: []
    created_at: '2026-05-24T00:00:00Z'
    last_updated: '2026-05-24T00:00:00Z'
    state_changed_at: '2026-05-24T00:00:00Z'
non_goals: []
"""
    )

    with caplog.at_level(logging.WARNING, logger="jig.ticket_mcp"):
        await handle_create_ticket(
            tickets=tickets,
            bus=bus,
            sender="pm",
            args={
                "work_type": "feature",
                "title": "ungated",
                "description": "x\n\n" + TICKET_AC_PLACEHOLDER,
                "derived_from": "project://spec/capabilities/empty-cap",
            },
            project_path=project,
        )

    assert any(
        "materialised with no acceptance_criteria" in r.message for r in caplog.records
    )


@pytest.mark.asyncio
async def test_create_ticket_with_no_project_spec_skips_silently(
    stores, tmp_path: Path
) -> None:
    """If project.structured.yaml is absent, materialisation is skipped
    without raising. Ticket creation succeeds."""
    from jig.specs import load_ticket_spec

    tickets, _threads, bus = stores
    project = _initialised_project(tmp_path / "proj")
    # No _write_project_spec call — file is intentionally absent.

    ticket_id = await handle_create_ticket(
        tickets=tickets,
        bus=bus,
        sender="pm",
        args={
            "work_type": "feature",
            "title": "Foo",
            "description": "x\n\n" + TICKET_AC_PLACEHOLDER,
            "derived_from": "project://spec/capabilities/anything",
        },
        project_path=project,
    )
    assert await tickets.get(ticket_id) is not None
    assert load_ticket_spec(project, ticket_id) is None
