"""Epic 3 MVP task 3 — per-agent MCP server assembly owned by the runtime.

``jig.runtime.mcp.build_agent_mcp_servers`` assembles the ``{name: server}`` map a
real agent run gets: the in-process ``jig`` server (via the tool-registration
factory that stays in ``jig.mcp_server``) plus any external stdio MCPs the role
allows. The isolation fitness test pins the evaluability north star: importing the
assembly must not drag in the orchestrator / daemon / TUI / app layer.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


from jig.project import Project
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.runtime.mcp import (
    build_agent_mcp_servers,
    effective_ticket_base_ref,
    resolve_external_mcps,
)
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _ctx(tmp_path: Path, *, allowed_mcps: list[str] | None = None) -> AgentSpawnContext:
    from jig.persistence import load_role

    role_cfg = load_role(tmp_path, "reviewer-generalist")
    if allowed_mcps is not None:
        role_cfg.allowed_mcps = allowed_mcps
    proj = Project(id="p", name="p", path=str(tmp_path), default_branch="develop")
    return AgentSpawnContext(
        role="reviewer-generalist",
        role_cfg=role_cfg,
        spawn_reason=SpawnReason.REVIEWER_FEDERATION,
        ticket=Ticket(
            id="t",
            work_type=WorkType.FEATURE,
            title="x",
            created_by="pm",
            description=TICKET_AC_PLACEHOLDER,
        ),
        parent=None,
        worktree_path=tmp_path,
        project=proj,
        tickets=None,  # type: ignore[arg-type]
        threads=None,  # type: ignore[arg-type]
        memory=None,  # type: ignore[arg-type]
        bus=None,  # type: ignore[arg-type]
    )


def test_build_always_includes_the_jig_server(tmp_path: Path) -> None:
    servers = build_agent_mcp_servers(_ctx(tmp_path), can_waive=frozenset())
    assert "jig" in servers
    assert servers["jig"] is not None


def test_build_with_no_allowed_mcps_is_jig_only(tmp_path: Path) -> None:
    servers = build_agent_mcp_servers(
        _ctx(tmp_path, allowed_mcps=[]), can_waive=frozenset()
    )
    assert list(servers) == ["jig"]


def _patch_home(monkeypatch, home: Path) -> None:
    # Patch the Path symbol the module actually uses (more robust across Python
    # versions than mutating stdlib Path.home).
    import jig.runtime.mcp as mcp_mod

    class _Path(type(home)):  # type: ignore[misc]
        @classmethod
        def home(cls) -> Path:
            return home

    monkeypatch.setattr(mcp_mod, "Path", _Path)


def test_build_merges_external_mcps(tmp_path: Path, monkeypatch) -> None:
    # A role-allowed external MCP resolved from ~/.claude/.mcp.json is merged
    # alongside the jig server.
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"weather": {"command": "weather-mcp"}}})
    )
    _patch_home(monkeypatch, home)

    servers = build_agent_mcp_servers(
        _ctx(tmp_path, allowed_mcps=["weather"]), can_waive=frozenset()
    )
    assert set(servers) == {"jig", "weather"}
    assert servers["weather"] == {"command": "weather-mcp"}


def test_resolve_external_mcps_empty_is_empty() -> None:
    assert resolve_external_mcps([]) == {}


def test_resolve_external_mcps_tolerates_malformed_config(
    tmp_path: Path, monkeypatch
) -> None:
    # A non-dict root (here a JSON array) must be skipped, not crash the spawn.
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".mcp.json").write_text(json.dumps(["not", "an", "object"]))
    _patch_home(monkeypatch, home)

    assert resolve_external_mcps(["weather"]) == {}


def test_effective_ticket_base_ref_prefers_delta_base(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    assert effective_ticket_base_ref(ctx) == "develop"  # no delta_base -> default
    ctx.delta_base = "abc123"
    assert effective_ticket_base_ref(ctx) == "abc123"


# --- isolation fitness function (the evaluability north star) -----------------

# The app/daemon/TUI layer the headless pipeline must NOT need to assemble agents.
_FORBIDDEN = ("jig.orchestrator", "jig.ws_server", "jig.container", "jig.daemon")


def test_importing_the_assembly_does_not_pull_in_the_app_layer() -> None:
    """Importing + using ``jig.runtime.mcp`` must not import the orchestrator /
    daemon / app layer. Run in a fresh interpreter so an already-imported
    orchestrator in this test session can't mask a real dependency."""
    forbidden = ", ".join(repr(m) for m in _FORBIDDEN)
    script = (
        "import sys\n"
        "import jig.runtime.mcp as m\n"
        "assert hasattr(m, 'build_agent_mcp_servers')\n"
        f"leaked = [n for n in ({forbidden},) if n in sys.modules]\n"
        "assert not leaked, 'app-layer modules leaked into the runtime: ' "
        "+ repr(leaked)\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout


def test_tui_layer_is_not_pulled_in() -> None:
    # Separate (and stricter): the TUI package must not be imported either.
    script = (
        "import sys\n"
        "import jig.runtime.mcp\n"
        "leaked = [n for n in sys.modules if n == 'jig.tui' "
        "or n.startswith('jig.tui.')]\n"
        "assert not leaked, leaked\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in proc.stdout
