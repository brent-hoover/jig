"""Tests for the reviewer-scope MCP tools (``reviewer_get_diff`` and,
in a later step, ``reviewer_read_file``).

These tools replace ``Bash(git diff*)`` and ``Read`` for scoped
reviewers. The orchestrator-side enforcement is what makes
``reads_glob`` actually load-bearing — without it the reviewer
could shell out and see everything regardless of role config.

The tests build a tmp_path git repo with content under both
``src/`` and ``tests/`` directories, register the MCP tool via
``create_agent_mcp_server``, invoke it directly through the
captured tool function, and assert that the response respects
the scope.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import jig.mcp_server as mcp_server_mod
from jig.mcp_server import create_agent_mcp_server
from jig.models import RoleConfig
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


@pytest.fixture
async def stores(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    return tickets, threads, memory, bus


def _spy_factory(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    real = mcp_server_mod.create_sdk_mcp_server

    def spy(*, name: str, tools: list) -> object:
        captured["tools"] = tools
        return real(name=name, tools=tools)

    monkeypatch.setattr(mcp_server_mod, "create_sdk_mcp_server", spy)


def _get_tool(captured: dict, name: str):
    for t in captured["tools"]:
        if t.name == name:
            return t
    raise AssertionError(
        f"tool {name!r} not registered; got {[t.name for t in captured['tools']]}"
    )


def _seed_repo(worktree: Path) -> str:
    """Initialise a git repo with content under src/ and tests/.

    Commit twice so there's a diff between HEAD~1 and HEAD. The
    second commit adds files to both subdirs — that's what the
    reviewer would see on its ticket worktree.
    """
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=worktree, check=True, env=env
    )
    (worktree / "src").mkdir()
    (worktree / "tests").mkdir()
    (worktree / "src" / "existing.py").write_text("# baseline src\n")
    (worktree / "tests" / "existing_test.py").write_text("# baseline tests\n")
    subprocess.run(["git", "add", "."], cwd=worktree, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-qm", "baseline"], cwd=worktree, check=True, env=env
    )
    (worktree / "src" / "feature.py").write_text(
        "def added_in_ticket():\n    return 'src side'\n"
    )
    (worktree / "tests" / "feature_test.py").write_text(
        "def test_added_in_ticket():\n    pass\n"
    )
    subprocess.run(["git", "add", "."], cwd=worktree, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-qm", "ticket-changes"],
        cwd=worktree,
        check=True,
        env=env,
    )
    return "HEAD~1"


async def _invoke(tool, **kwargs) -> dict:
    """Call an MCP tool function and decode its JSON response."""
    result = await tool.handler(kwargs)
    text = result["content"][0]["text"]
    return json.loads(text)


@pytest.mark.asyncio
async def test_reviewer_get_diff_scopes_to_src_only(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewer with ``reads_glob: ['src/**']`` sees only the
    src-side diff. The tests/ changes are invisible."""
    tickets, threads, memory, bus = stores
    _seed_repo(tmp_path)
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_get_diff"],
        reads_glob=["src/**"],
        reads_exclude=["tests/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    tool = _get_tool(captured, "reviewer_get_diff")
    payload = await _invoke(tool, base="HEAD~1")
    assert "diff" in payload, f"expected diff in payload; got {payload}"
    diff = payload["diff"]
    # src-side change is visible.
    assert "src/feature.py" in diff
    assert "added_in_ticket" in diff
    # tests-side change must NOT appear in the diff text.
    assert "tests/feature_test.py" not in diff, (
        f"tests/ file leaked into scoped diff:\n{diff}"
    )
    assert "test_added_in_ticket" not in diff
    # ``scope`` echoes the pathspec for the reviewer's log.
    assert payload["scope"] == [":(glob)src/**", ":(exclude,glob)tests/**"]


@pytest.mark.asyncio
async def test_reviewer_get_diff_scopes_to_tests_only(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric: a reviewer scoped to ``tests/**`` sees tests but
    not src."""
    tickets, threads, memory, bus = stores
    _seed_repo(tmp_path)
    cfg = RoleConfig(
        role="reviewer-test-adequacy",
        allowed_tools=["reviewer_get_diff"],
        reads_glob=["tests/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    tool = _get_tool(captured, "reviewer_get_diff")
    payload = await _invoke(tool, base="HEAD~1")
    diff = payload["diff"]
    assert "tests/feature_test.py" in diff
    assert "src/feature.py" not in diff


@pytest.mark.asyncio
async def test_reviewer_get_diff_uses_threaded_base_ref(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ticket_base_ref`` passed by the orchestrator at spawn-time
    is the authoritative diff base. Without it the tool would
    fall back to env vars or branch probes which can be wrong on
    chained / fix-loop tickets."""
    tickets, threads, memory, bus = stores
    _seed_repo(tmp_path)
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_get_diff"],
        reads_glob=["src/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
        ticket_base_ref="HEAD~1",
    )
    tool = _get_tool(captured, "reviewer_get_diff")
    # Call with NO ``base`` arg — the threaded base must be used.
    payload = await _invoke(tool)
    assert "diff" in payload
    assert "src/feature.py" in payload["diff"]


@pytest.mark.asyncio
async def test_reviewer_get_diff_uses_merge_base_for_threaded_ref(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``ticket_base_ref`` names a branch (not a SHA), the
    tool computes ``git merge-base <branch> HEAD`` and diffs
    against the divergence point — not against the current tip
    of the branch. This pins the ticket-only diff even if other
    tickets merge into the base branch between worktree creation
    and review.
    """
    tickets, threads, memory, bus = stores
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, env=env
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "baseline.py").write_text("# baseline\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True, env=env
    )
    # Save the baseline SHA — that's the divergence point we expect
    # merge-base to find.
    baseline_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # The ticket's branch makes a change.
    subprocess.run(
        ["git", "checkout", "-qb", "jig/t-a"], cwd=tmp_path, check=True, env=env
    )
    (tmp_path / "src" / "ticket.py").write_text("def t(): return 1\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-qm", "ticket change"], cwd=tmp_path, check=True, env=env
    )
    # ``main`` then advances with unrelated work (simulating a
    # parallel ticket merging in).
    subprocess.run(["git", "checkout", "-q", "main"], cwd=tmp_path, check=True, env=env)
    (tmp_path / "src" / "unrelated.py").write_text("# unrelated\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-qm", "unrelated"], cwd=tmp_path, check=True, env=env
    )
    subprocess.run(
        ["git", "checkout", "-q", "jig/t-a"], cwd=tmp_path, check=True, env=env
    )

    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_get_diff"],
        reads_glob=["src/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
        ticket_base_ref="main",
    )
    tool = _get_tool(captured, "reviewer_get_diff")
    payload = await _invoke(tool)
    # The diff must contain the ticket's change AND must NOT
    # contain the unrelated change merged into main after worktree
    # creation. Diffing against main's tip would have shown
    # ``unrelated.py`` as a deletion; diffing against the
    # merge-base correctly omits it.
    assert "diff" in payload
    assert "ticket.py" in payload["diff"]
    assert "unrelated.py" not in payload["diff"], (
        "merge-base failed — got tip-of-main diff including unrelated"
    )
    # Sanity: the actual diff base used is the baseline SHA (the
    # divergence point), surfaced via the original ticket_base_ref
    # in the response scope's call chain.
    _ = baseline_sha  # documenting expected divergence SHA


@pytest.mark.asyncio
async def test_reviewer_get_diff_arg_overrides_threaded_base(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit ``base`` arg wins over the orchestrator-supplied
    default. Lets the reviewer adjust if it needs a different
    base for some reason (rare, but the override exists)."""
    tickets, threads, memory, bus = stores
    _seed_repo(tmp_path)
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_get_diff"],
        reads_glob=["src/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
        ticket_base_ref="some-bogus-default-that-would-fail",
    )
    tool = _get_tool(captured, "reviewer_get_diff")
    payload = await _invoke(tool, base="HEAD~1")
    # Explicit base wins, diff succeeds.
    assert "diff" in payload
    assert "src/feature.py" in payload["diff"]


@pytest.mark.asyncio
async def test_reviewer_get_diff_fails_loud_on_bad_base(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-existent base ref must surface as an error in the
    payload — silent fallback to unscoped diff would defeat the
    enforcement."""
    tickets, threads, memory, bus = stores
    _seed_repo(tmp_path)
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_get_diff"],
        reads_glob=["src/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    tool = _get_tool(captured, "reviewer_get_diff")
    payload = await _invoke(tool, base="definitely-not-a-ref")
    assert "error" in payload
    assert "definitely-not-a-ref" in payload["error"]


@pytest.mark.asyncio
async def test_reviewer_get_diff_not_registered_when_not_in_allowed_tools(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tool is opt-in via allowed_tools, like every other
    reviewer tool. Legacy roles that don't list it don't see it."""
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="reviewer-legacy",
        allowed_tools=["reviewer_post_comment"],  # no reviewer_get_diff
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = {t.name for t in captured["tools"]}
    assert "reviewer_get_diff" not in names


# ---------------------------------------------------------------------------
# reviewer_read_file
# ---------------------------------------------------------------------------


def _setup_read_repo(worktree: Path) -> None:
    """Lay out src/ + tests/ files for read-scope tests."""
    (worktree / "src").mkdir()
    (worktree / "tests").mkdir()
    (worktree / "src" / "api.py").write_text("# src content\n")
    (worktree / "tests" / "test_api.py").write_text("# test content\n")
    (worktree / "pyproject.toml").write_text("[project]\nname = 'p'\n")


@pytest.mark.asyncio
async def test_reviewer_read_file_returns_in_scope_content(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An in-scope read returns the file content."""
    tickets, threads, memory, bus = stores
    _setup_read_repo(tmp_path)
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_read_file"],
        reads_glob=["src/**", "pyproject.toml"],
        reads_exclude=["tests/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    tool = _get_tool(captured, "reviewer_read_file")
    payload = await _invoke(tool, path="src/api.py")
    assert payload == {"content": "# src content\n"}
    # The non-src-but-in-scope file also reads.
    payload = await _invoke(tool, path="pyproject.toml")
    assert "name = 'p'" in payload["content"]


@pytest.mark.asyncio
async def test_reviewer_read_file_refuses_out_of_scope(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An out-of-scope read returns ``error`` and does NOT leak
    content. The error message echoes the scope so the reviewer
    can tell what IS available."""
    tickets, threads, memory, bus = stores
    _setup_read_repo(tmp_path)
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_read_file"],
        reads_glob=["src/**"],
        reads_exclude=["tests/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    tool = _get_tool(captured, "reviewer_read_file")
    payload = await _invoke(tool, path="tests/test_api.py")
    assert "error" in payload
    assert "content" not in payload
    assert "tests/test_api.py" in payload["error"]
    # The error mentions the scope so the LLM can re-orient.
    assert "src/**" in payload["error"]


@pytest.mark.asyncio
async def test_reviewer_read_file_refuses_absolute_and_traversal(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absolute paths and traversal sequences (``..``) are refused
    by ``path_in_scope`` regardless of glob, so the tool can never
    be coerced into reading outside the worktree."""
    tickets, threads, memory, bus = stores
    _setup_read_repo(tmp_path)
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_read_file"],
        reads_glob=["**"],  # widest possible — should still refuse
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    tool = _get_tool(captured, "reviewer_read_file")

    for evil in ("/etc/passwd", "../outside.py", "src/../etc/passwd"):
        payload = await _invoke(tool, path=evil)
        assert "error" in payload, f"path {evil!r} was NOT refused; got {payload}"
        assert "content" not in payload


@pytest.mark.asyncio
async def test_reviewer_read_file_refuses_symlink_to_excluded(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink at an in-scope path that targets an excluded path
    must be refused after resolution. The reviewer can't route
    around the exclude-set by planting a symlink."""
    tickets, threads, memory, bus = stores
    _setup_read_repo(tmp_path)
    # Plant a symlink at src/leak → tests/test_api.py.
    (tmp_path / "src" / "leak").symlink_to(tmp_path / "tests" / "test_api.py")
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_read_file"],
        reads_glob=["src/**"],
        reads_exclude=["tests/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    tool = _get_tool(captured, "reviewer_read_file")
    payload = await _invoke(tool, path="src/leak")
    assert "error" in payload
    assert "content" not in payload
    # Error message names the resolved target so the reviewer can
    # see what happened, not just "denied."
    assert "tests/test_api.py" in payload["error"]


@pytest.mark.asyncio
async def test_reviewer_read_file_refuses_symlink_outside_worktree(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink pointing outside the worktree boundary (e.g. to
    /etc/passwd or a parent dir) must be refused. Worktree
    containment is a load-bearing security boundary."""
    tickets, threads, memory, bus = stores
    _setup_read_repo(tmp_path)
    # Create a file outside the worktree and a symlink into it.
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secrets\n")
    (tmp_path / "src" / "escape").symlink_to(outside)
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_read_file"],
        reads_glob=["src/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    tool = _get_tool(captured, "reviewer_read_file")
    payload = await _invoke(tool, path="src/escape")
    assert "error" in payload
    assert "content" not in payload
    assert "worktree boundary" in payload["error"]


@pytest.mark.asyncio
async def test_reviewer_read_file_missing_in_scope_returns_error(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An in-scope path that doesn't exist in the worktree returns
    a specific error (not a Python traceback)."""
    tickets, threads, memory, bus = stores
    _setup_read_repo(tmp_path)
    cfg = RoleConfig(
        role="reviewer-pattern-conformance",
        allowed_tools=["reviewer_read_file"],
        reads_glob=["src/**"],
    )
    captured: dict = {}
    _spy_factory(monkeypatch, captured)
    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role=cfg.role,
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    tool = _get_tool(captured, "reviewer_read_file")
    payload = await _invoke(tool, path="src/missing.py")
    assert "error" in payload
    assert "does not exist" in payload["error"]
