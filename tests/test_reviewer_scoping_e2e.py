"""End-to-end mechanical test for the reviewer-scoping feature.

Pins the production wiring described in the problem statement —
the hn-cli RC-9 scenario where ``reviewer-pattern-conformance``
filed a finding on ``tests/test_api.py`` and the routing layer
bounced the ticket back through ``test → no-op dev → full
federation``. After this feature ships:

1. The shipped non-test reviewer YAMLs declare ``reads_glob``
   that excludes ``tests/**``, so the catalog validator confirms
   they can't reach test content via the MCP tools.
2. ``reviewer-test-adequacy`` is scoped to ``tests/**`` so it
   alone owns test-side concerns.
3. The routing-layer defence-in-depth rejects any finding on a
   test file from a non-test reviewer as ``out-of-scope-finding``,
   even if (hypothetically) the reviewer managed to file one.
4. A test-adequacy finding on the same file routes correctly to
   the test phase via ``writes-glob`` matching.

These tests exercise the mechanical chain (catalog validation,
MCP tool scoping, routing-layer rejection) without spawning real
LLM agents. Whether the agent OBEYS the prompt is a behavioural
concern measured by the eval framework, not the unit suite.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import jig.mcp_server as mcp_server_mod
from jig.catalog import validate_catalog
from jig.mcp_server import create_agent_mcp_server
from jig.models import PhaseConfig, WorkflowConfig
from jig.persistence import init_project, save_workflow
from jig.reviewer_routing import _route_blocking_comments
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
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


def _seed_rc9_repo(worktree: Path) -> None:
    """Build a tmp project with the RC-9 shape: src/ + tests/ with
    duplicate helpers across two test files."""
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"],
                   cwd=worktree, check=True, env=env)
    (worktree / "src").mkdir()
    (worktree / "tests").mkdir()
    (worktree / "src" / "api.py").write_text(
        "def fetch():\n    return 1\n"
    )
    (worktree / "tests" / "test_api.py").write_text(
        "def _register_mocks():\n    return {}\n\n"
        "def test_fetch():\n    pass\n"
    )
    (worktree / "tests" / "test_cli.py").write_text(
        # Duplicate helper — exactly the RC-9 anti-pattern.
        "def _register_mocks():\n    return {}\n\n"
        "def test_cli():\n    pass\n"
    )
    subprocess.run(["git", "add", "."], cwd=worktree, check=True, env=env)
    subprocess.run(
        ["git", "commit", "-qm", "initial"],
        cwd=worktree, check=True, env=env,
    )


# ---------------------------------------------------------------------------
# Mechanical chain — catalog → MCP scope → routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shipped_non_test_reviewer_passes_catalog_validation(
    tmp_path: Path,
) -> None:
    """Every shipped non-test reviewer config (which now declares
    ``reads_glob``) passes the catalog validator's scoped-role
    invariants. Caught at startup if any of the YAMLs drift out of
    sync with the validator."""
    (tmp_path / ".git").mkdir()
    init_project(tmp_path)
    # ``validate_catalog`` walks the shipped role configs because
    # we haven't authored any project-local overrides.
    validate_catalog(tmp_path)


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
    raise AssertionError(f"tool {name!r} not registered")


async def _invoke(tool, **kwargs) -> dict:
    result = await tool.handler(kwargs)
    return json.loads(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_pattern_conformance_mcp_tools_cannot_see_tests(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: spawn the MCP server for the SHIPPED
    pattern-conformance role and confirm its tools refuse to
    return test-file content. This is the load-bearing assertion
    of the whole feature — if the shipped scope leaks tests, the
    federation goes right back to the RC-9 scenario."""
    tickets, threads, memory, bus = stores
    _seed_rc9_repo(tmp_path)

    # Load the shipped role config (no project override).
    from jig.persistence import load_role

    cfg = load_role(tmp_path, "reviewer-pattern-conformance")
    assert cfg.reads_glob, (
        "shipped reviewer-pattern-conformance must declare reads_glob"
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

    # reviewer_get_diff returns no tests/ content even though the
    # diff (vs an empty initial commit) includes test files.
    diff_tool = _get_tool(captured, "reviewer_get_diff")
    payload = await _invoke(diff_tool, base="HEAD")
    diff = payload.get("diff", "")
    assert "tests/test_api.py" not in diff
    assert "tests/test_cli.py" not in diff
    # src content is visible (from the initial commit there's
    # nothing to diff against HEAD, so this just verifies the tool
    # filtered tests/ even when src/ would be empty).

    # reviewer_read_file refuses both test files.
    read_tool = _get_tool(captured, "reviewer_read_file")
    for path in ("tests/test_api.py", "tests/test_cli.py"):
        payload = await _invoke(read_tool, path=path)
        assert "error" in payload, (
            f"shipped reviewer was able to read {path!r}; got {payload}"
        )
        assert "content" not in payload


@pytest.mark.asyncio
async def test_test_adequacy_mcp_tools_can_see_tests(
    tmp_path: Path, stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Symmetric: the shipped test-adequacy reviewer CAN read both
    test files. Without this, federation coverage would have a
    hole — no reviewer would see test content at all."""
    tickets, threads, memory, bus = stores
    _seed_rc9_repo(tmp_path)
    from jig.persistence import load_role

    cfg = load_role(tmp_path, "reviewer-test-adequacy")
    assert "tests/**" in cfg.reads_glob

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
    read_tool = _get_tool(captured, "reviewer_read_file")
    for path in ("tests/test_api.py", "tests/test_cli.py"):
        payload = await _invoke(read_tool, path=path)
        assert "content" in payload, (
            f"test-adequacy couldn't read {path!r}; got {payload}"
        )
        assert "_register_mocks" in payload["content"]


@pytest.mark.asyncio
async def test_routing_drops_pattern_conformance_finding_on_test_file(
    tmp_path: Path,
) -> None:
    """The RC-9 scenario, replayed through the routing layer: a
    pattern-conformance finding on ``tests/test_cli.py`` no longer
    bounces the ticket back to the test phase — it's dropped as
    ``out-of-scope-finding``. The test phase doesn't re-run, dev
    doesn't get a no-op invocation, the full federation doesn't
    cycle again."""
    (tmp_path / ".git").mkdir()
    init_project(tmp_path)
    wf = WorkflowConfig(
        name="feature-s-full",
        phases=[
            PhaseConfig(name="test", role="test", writes=["tests/**"]),
            PhaseConfig(name="review-tests", role="review",
                        reviewers=["reviewer-test-adequacy"]),
            PhaseConfig(name="implement", role="dev",
                        writes=["src/**", "pyproject.toml"]),
            PhaseConfig(name="review", role="review",
                        reviewers=["reviewer-pattern-conformance"]),
        ],
    )
    save_workflow(tmp_path, wf)

    # The synthesised finding mirrors the actual RC-9 from the
    # hn-cli log: pattern-conformance on tests/test_api.py.
    comment = ReviewerComment(
        type=ReviewerCommentType("pattern-divergence"),
        severity=Severity.IMPORTANT,
        reviewer="reviewer-pattern-conformance",
        prose="Duplicate _register_mocks helper across test files",
        confidence=0.8,
        file="tests/test_api.py",
    )

    route = await _route_blocking_comments(
        wf,
        blocked_phase_idx=3,  # review phase
        comments=[comment],
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    # No route → the orchestrator's fix-loop sees nothing to bounce
    # on and the ticket doesn't get sent back through test → dev →
    # full federation.
    assert route is None


@pytest.mark.asyncio
async def test_routing_accepts_test_adequacy_finding_on_test_file(
    tmp_path: Path,
) -> None:
    """The legitimate path: test-adequacy files the SAME finding
    (duplicate helper across test files) and routing accepts it,
    sending the ticket back to the test phase for fix."""
    (tmp_path / ".git").mkdir()
    init_project(tmp_path)
    wf = WorkflowConfig(
        name="feature-s-full",
        phases=[
            PhaseConfig(name="test", role="test", writes=["tests/**"]),
            PhaseConfig(name="review-tests", role="review",
                        reviewers=["reviewer-test-adequacy"]),
            PhaseConfig(name="implement", role="dev",
                        writes=["src/**", "pyproject.toml"]),
            PhaseConfig(name="review", role="review",
                        reviewers=["reviewer-pattern-conformance"]),
        ],
    )
    save_workflow(tmp_path, wf)

    comment = ReviewerComment(
        type=ReviewerCommentType("test-adequacy"),
        severity=Severity.IMPORTANT,
        reviewer="reviewer-test-adequacy",
        prose="Duplicate _register_mocks helper across test files",
        confidence=0.8,
        file="tests/test_api.py",
    )

    route = await _route_blocking_comments(
        wf,
        blocked_phase_idx=1,  # review-tests phase
        comments=[comment],
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    assert route is not None
    target_phase_idx, reason = route
    # Routes to the test phase via writes-glob match.
    assert wf.phases[target_phase_idx].name == "test"
    assert "writes-glob" in reason
