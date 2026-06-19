"""Tests for jig.hooks + jig.project HooksConfig."""

import asyncio
import os
import stat
import subprocess
from pathlib import Path

import pytest

from jig.hooks import (
    HOOK_NAMES,
    HOOK_SCRIPTS,
    SENTINEL_LINE,
    HookInstallError,
    _git_common_dir,
    _is_jig_managed,
    _resolve_ticket_worktree,
    hook_status,
    install_hooks,
    run_pre_commit,
    run_pre_push,
    uninstall_hooks,
)
from jig.project import HooksConfig, Project, load_project, save_project


def test_hooks_config_defaults():
    cfg = HooksConfig()
    assert cfg.pre_push_command is None


def test_project_has_hooks_field_with_default():
    project = Project(id="p1", name="p1", path="/tmp/p1")
    assert isinstance(project.hooks, HooksConfig)
    assert project.hooks.pre_push_command is None


def test_project_roundtrip_with_hooks(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    project = Project(
        id="p1",
        name="p1",
        path=str(tmp_path),
        hooks=HooksConfig(pre_push_command="uv run pytest -q"),
    )
    save_project(tmp_path, project)
    loaded = load_project(tmp_path)
    assert loaded.hooks.pre_push_command == "uv run pytest -q"


def test_project_legacy_config_without_hooks_block_loads_cleanly(tmp_path: Path):
    """Existing .jig/config.yaml files with no 'hooks:' section must keep working."""
    (tmp_path / ".jig").mkdir()
    (tmp_path / ".jig" / "config.yaml").write_text(
        "project:\n  id: legacy\n  name: legacy\n  path: " + str(tmp_path) + "\n"
    )
    loaded = load_project(tmp_path)
    assert isinstance(loaded.hooks, HooksConfig)
    assert loaded.hooks.pre_push_command is None


def test_hook_names_is_canonical_triple():
    assert HOOK_NAMES == ("pre-commit", "pre-push", "commit-msg")


def test_hook_scripts_cover_every_hook():
    for name in HOOK_NAMES:
        assert name in HOOK_SCRIPTS
        script = HOOK_SCRIPTS[name]
        assert script.startswith("#!/usr/bin/env bash")
        # Sentinel must be byte-exact on line 2.
        assert script.splitlines()[1] == SENTINEL_LINE
        # Every script forwards to `jig hooks run <name>`.
        assert f"jig hooks run {name}" in script


def test_is_jig_managed_detects_sentinel(tmp_path: Path):
    managed = tmp_path / "pre-commit"
    managed.write_text(f"#!/usr/bin/env bash\n{SENTINEL_LINE}\nexit 0\n")
    assert _is_jig_managed(managed) is True


def test_is_jig_managed_rejects_foreign_hook(tmp_path: Path):
    foreign = tmp_path / "pre-commit"
    foreign.write_text("#!/usr/bin/env bash\necho hi\nexit 0\n")
    assert _is_jig_managed(foreign) is False


def test_is_jig_managed_rejects_missing_file(tmp_path: Path):
    assert _is_jig_managed(tmp_path / "missing") is False


def test_is_jig_managed_rejects_short_file(tmp_path: Path):
    short = tmp_path / "pre-commit"
    short.write_text("#!/usr/bin/env bash\n")
    assert _is_jig_managed(short) is False


def test_is_jig_managed_follows_symlink(tmp_path: Path):
    real = tmp_path / "real"
    real.write_text("#!/usr/bin/env bash\necho foreign\n")
    link = tmp_path / "pre-commit"
    link.symlink_to(real)
    assert _is_jig_managed(link) is False


def test_is_jig_managed_rejects_empty_file(tmp_path: Path):
    empty = tmp_path / "pre-commit"
    empty.touch()
    assert _is_jig_managed(empty) is False


def test_is_jig_managed_rejects_binary_file(tmp_path: Path):
    binary = tmp_path / "pre-commit"
    binary.write_bytes(b"\x7fELF\x02\x01\x01\x00\x00\x00" * 10)
    assert _is_jig_managed(binary) is False


def _git_init(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True
    )


def test_git_common_dir_in_main_repo(tmp_path: Path):
    _git_init(tmp_path)
    common = _git_common_dir(tmp_path)
    assert common == (tmp_path / ".git").resolve()


def test_git_common_dir_raises_outside_repo(tmp_path: Path):
    with pytest.raises(RuntimeError, match="not inside a git repository"):
        _git_common_dir(tmp_path)


def test_resolve_ticket_worktree_detects_jig_layout(tmp_path: Path):
    _git_init(tmp_path)
    # Simulate jig's layout: <project>/.jig/worktrees/<ticket_id>/
    wt = tmp_path / ".jig" / "worktrees" / "t-123"
    wt.mkdir(parents=True)
    # Real jig worktrees have a .git FILE (not dir) — mimic that.
    (wt / ".git").write_text("gitdir: ../../../.git/worktrees/t-123\n")
    ticket_id = _resolve_ticket_worktree(wt)
    assert ticket_id == "t-123"


def test_resolve_ticket_worktree_returns_none_for_main_repo(tmp_path: Path):
    _git_init(tmp_path)
    assert _resolve_ticket_worktree(tmp_path) is None


def test_resolve_ticket_worktree_false_positive_guard(tmp_path: Path):
    """A dir literally named 'worktrees' without the .jig parent must not match."""
    _git_init(tmp_path)
    sneaky = tmp_path / "worktrees" / "t-fake"
    sneaky.mkdir(parents=True)
    assert _resolve_ticket_worktree(sneaky) is None


def test_install_hooks_fresh_writes_all_three(tmp_path: Path):
    _git_init(tmp_path)
    report = install_hooks(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    for name in HOOK_NAMES:
        target = hooks_dir / name
        assert target.is_file(), f"{name} not installed"
        assert _is_jig_managed(target)
        mode = os.stat(target).st_mode
        assert mode & stat.S_IXUSR, f"{name} not executable"
    assert all("installed" in line for line in report)


def test_install_hooks_refresh_over_jig_managed(tmp_path: Path):
    _git_init(tmp_path)
    install_hooks(tmp_path)
    # Second run refreshes silently, no .jig-backup files.
    report = install_hooks(tmp_path)
    assert all("refreshed" in line for line in report)
    hooks_dir = tmp_path / ".git" / "hooks"
    assert not (hooks_dir / "pre-commit.jig-backup").exists()


def test_install_hooks_backs_up_existing_foreign_hook(tmp_path: Path):
    _git_init(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    original = "#!/usr/bin/env bash\necho 'user hook'\n"
    (hooks_dir / "pre-commit").write_text(original)
    os.chmod(hooks_dir / "pre-commit", 0o755)

    report = install_hooks(tmp_path)

    backup = hooks_dir / "pre-commit.jig-backup"
    assert backup.is_file()
    assert backup.read_text() == original
    assert _is_jig_managed(hooks_dir / "pre-commit")
    assert any("backed up" in line for line in report)


def test_install_hooks_refuses_when_backup_collision(tmp_path: Path):
    _git_init(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "pre-commit").write_text("#!/usr/bin/env bash\necho one\n")
    (hooks_dir / "pre-commit.jig-backup").write_text(
        "#!/usr/bin/env bash\necho earlier\n"
    )

    with pytest.raises(HookInstallError, match="backup already exists"):
        install_hooks(tmp_path)


def test_install_hooks_force_overwrites_backup(tmp_path: Path):
    _git_init(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    new_foreign = "#!/usr/bin/env bash\necho new\n"
    (hooks_dir / "pre-commit").write_text(new_foreign)
    (hooks_dir / "pre-commit.jig-backup").write_text("#!/usr/bin/env bash\necho old\n")

    install_hooks(tmp_path, force=True)

    assert (hooks_dir / "pre-commit.jig-backup").read_text() == new_foreign
    assert _is_jig_managed(hooks_dir / "pre-commit")


def test_uninstall_hooks_removes_jig_managed_without_backup(tmp_path: Path):
    _git_init(tmp_path)
    install_hooks(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    report = uninstall_hooks(tmp_path)
    for name in HOOK_NAMES:
        assert not (hooks_dir / name).exists()
    assert all("uninstalled" in line for line in report)


def test_uninstall_hooks_restores_backup(tmp_path: Path):
    _git_init(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    original = "#!/usr/bin/env bash\necho user hook\n"
    (hooks_dir / "pre-commit").write_text(original)
    install_hooks(tmp_path)

    uninstall_hooks(tmp_path)

    assert (hooks_dir / "pre-commit").read_text() == original
    assert not (hooks_dir / "pre-commit.jig-backup").exists()


def test_uninstall_hooks_leaves_foreign_alone(tmp_path: Path):
    _git_init(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    foreign = "#!/usr/bin/env bash\necho foreign\n"
    (hooks_dir / "pre-commit").write_text(foreign)

    report = uninstall_hooks(tmp_path)

    assert (hooks_dir / "pre-commit").read_text() == foreign
    assert any("skipped" in line and "pre-commit" in line for line in report)


def test_uninstall_hooks_reports_missing(tmp_path: Path):
    _git_init(tmp_path)
    report = uninstall_hooks(tmp_path)
    assert all("not installed" in line for line in report)


def test_hook_status_all_installed(tmp_path: Path):
    _git_init(tmp_path)
    install_hooks(tmp_path)
    lines = hook_status(tmp_path)
    assert len(lines) == 3
    for name in HOOK_NAMES:
        assert any(
            line.startswith(name) and "installed (jig-managed)" in line
            for line in lines
        )


def test_hook_status_mixed_states(tmp_path: Path):
    _git_init(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    # pre-commit: jig-managed.
    install_hooks(tmp_path)
    # pre-push: foreign.
    (hooks_dir / "pre-push").unlink()
    (hooks_dir / "pre-push").write_text("#!/usr/bin/env bash\necho foreign\n")
    # commit-msg: not installed.
    (hooks_dir / "commit-msg").unlink()

    lines = hook_status(tmp_path)
    joined = "\n".join(lines)
    assert "pre-commit" in joined and "jig-managed" in joined
    assert "pre-push" in joined and "not jig-managed" in joined
    assert "commit-msg" in joined and "not installed" in joined


def _seed_catalog(project_path: Path, body: str) -> None:
    jig = project_path / ".jig"
    jig.mkdir(exist_ok=True)
    (jig / "checks.yaml").write_text(body)


def test_run_pre_commit_no_catalog_exits_zero(tmp_path: Path, capsys):
    # ``run_pre_commit`` now reads ``hook_eligible`` from each check
    # and only runs the marked ones. With no project-local
    # ``.jig/checks.yaml`` the loader still falls back to the shipped
    # catalog, but none of the shipped entries are hook-eligible —
    # so the hook is a no-op for unconfigured projects (matching
    # prior behaviour before the catalog fallback was added).
    _git_init(tmp_path)
    rc = asyncio.run(run_pre_commit(tmp_path))
    assert rc == 0
    assert "no hook-eligible required scripted checks" in capsys.readouterr().out


def test_run_pre_commit_shipped_catalog_not_hook_eligible(
    tmp_path: Path, capsys
) -> None:
    """The shipped catalog must NOT run on every commit.

    Regression guard: when ``load_check_catalog`` falls back to the
    shipped ``jig/defaults/checks.yaml``, none of the entries
    (``pytest-all``, ``mypy-strict``, ``pytest-new-tests-fail``,
    etc.) are hook_eligible — running them on every commit would
    catastrophically slow down operator workflows and the
    contradictory red/green pair would always fail.
    """
    _git_init(tmp_path)
    rc = asyncio.run(run_pre_commit(tmp_path))
    assert rc == 0
    # The shipped pytest entries didn't run — message confirms skip.
    assert "no hook-eligible" in capsys.readouterr().out


def test_run_pre_commit_runs_only_hook_eligible_checks(tmp_path: Path, capsys) -> None:
    """Mixed catalog: only entries with ``hook_eligible: true`` run."""
    _git_init(tmp_path)
    _seed_catalog(
        tmp_path,
        """
checks:
  fast-lint:
    type: scripted
    command: "true"
    severity: required
    hook_eligible: true
  slow-suite:
    type: scripted
    command: "exit 1"
    severity: required
""",
    )
    # If ``slow-suite`` ran, the hook would fail (it exits 1).
    rc = asyncio.run(run_pre_commit(tmp_path))
    assert rc == 0


def test_run_pre_commit_all_pass(tmp_path: Path, capsys):
    _git_init(tmp_path)
    _seed_catalog(
        tmp_path,
        """
checks:
  lint:
    type: scripted
    command: "true"
    severity: required
    hook_eligible: true
  format:
    type: scripted
    command: "true"
    severity: required
    hook_eligible: true
""",
    )
    rc = asyncio.run(run_pre_commit(tmp_path))
    assert rc == 0


def test_run_pre_commit_fail_lists_all_failures(tmp_path: Path, capsys):
    _git_init(tmp_path)
    _seed_catalog(
        tmp_path,
        """
checks:
  ok:
    type: scripted
    command: "true"
    severity: required
    hook_eligible: true
  broken:
    type: scripted
    command: "exit 3"
    severity: required
    hook_eligible: true
  alsobroken:
    type: scripted
    command: "exit 4"
    severity: required
    hook_eligible: true
""",
    )
    rc = asyncio.run(run_pre_commit(tmp_path))
    out = capsys.readouterr().out
    assert rc == 1
    assert "broken" in out
    assert "alsobroken" in out
    assert "--no-verify" in out


def test_run_pre_commit_skips_warning_severity(tmp_path: Path):
    _git_init(tmp_path)
    _seed_catalog(
        tmp_path,
        """
checks:
  soft:
    type: scripted
    command: "exit 1"
    severity: warning
    hook_eligible: true
""",
    )
    rc = asyncio.run(run_pre_commit(tmp_path))
    assert rc == 0


def test_run_pre_commit_skips_agent_checks(tmp_path: Path):
    _git_init(tmp_path)
    _seed_catalog(
        tmp_path,
        """
checks:
  review:
    type: implementation_aware_agent
    template: "review the diff"
    severity: required
    hook_eligible: true
""",
    )
    rc = asyncio.run(run_pre_commit(tmp_path))
    assert rc == 0


def _write_config_with_pre_push(tmp_path: Path, command: str | None) -> None:
    (tmp_path / ".jig").mkdir(exist_ok=True)
    hooks_block = f"\n  hooks:\n    pre_push_command: {command!r}\n" if command else ""
    (tmp_path / ".jig" / "config.yaml").write_text(
        f"project:\n  id: p1\n  name: p1\n  path: {tmp_path}\n{hooks_block}"
    )


def test_run_pre_push_outside_worktree_uses_fallback_command(tmp_path: Path):
    _git_init(tmp_path)
    _write_config_with_pre_push(tmp_path, "true")
    rc = asyncio.run(run_pre_push(tmp_path))
    assert rc == 0


def test_run_pre_push_outside_worktree_fallback_fails(tmp_path: Path):
    _git_init(tmp_path)
    _write_config_with_pre_push(tmp_path, "exit 7")
    rc = asyncio.run(run_pre_push(tmp_path))
    assert rc != 0


def test_run_pre_push_outside_worktree_no_fallback_skips(tmp_path: Path, capsys):
    _git_init(tmp_path)
    _write_config_with_pre_push(tmp_path, None)
    rc = asyncio.run(run_pre_push(tmp_path))
    assert rc == 0
    assert "pre_push_command not set" in capsys.readouterr().out


def test_run_pre_push_in_worktree_runs_phase_checks(tmp_path: Path, capsys):
    """Ticket worktree → reads ticket + workflow → runs current phase's scripted checks."""
    # Layout a real jig project with one ticket worktree.
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)
    _write_config_with_pre_push(project, None)
    (project / ".jig" / "checks.yaml").write_text("""
checks:
  lint:
    type: scripted
    command: "true"
    severity: required
""")
    (project / ".jig" / "workflows").mkdir()
    (project / ".jig" / "workflows" / "default.yaml").write_text("""
name: default
phases:
  - name: build
    role: dev
    automated_checks: [lint]
""")
    (project / ".jig" / "roles").mkdir()
    (project / ".jig" / "roles" / "dev.yaml").write_text("role: dev\n")

    # Create ticket record with the JsonlStore envelope so TicketStore.load()
    # actually picks it up — a bare dict raises ValueError on missing _op/_id
    # and the runner's narrowed except would silently fall through to the
    # fallback path, leaving the phase-aware branch uncovered.
    #
    # The Ticket model carries ``extra="forbid"`` (Block A.3) so the
    # JSONL must not duplicate ``id`` alongside ``_id`` — Pydantic
    # treats them as separate inputs and rejects the duplicate.
    (project / ".jig" / "store").mkdir()
    (project / ".jig" / "store" / "tickets.jsonl").write_text(
        '{"_op": "insert", "_id": "t-1", '
        '"work_type": "feature", "title": "x", "created_by": "u", '
        '"description": "## Acceptance criteria\\n- placeholder\\n", '
        '"workflow": "default"}\n'
    )

    # Simulate the worktree dir.
    wt = project / ".jig" / "worktrees" / "t-1"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {project / '.git' / 'worktrees' / 't-1'}\n")

    rc = asyncio.run(run_pre_push(wt))
    out = capsys.readouterr().out
    assert rc == 0
    # Positive evidence the phase-aware path ran: _run_scripted echoes the
    # lint command. If we'd fallen through to the fallback, we'd see the
    # "pre_push_command not set" skip message instead.
    assert "lint: true" in out
    assert "pre_push_command not set" not in out
    assert "not found" not in out


def test_run_pre_push_in_worktree_workflow_schema_invalid_skips(tmp_path: Path, capsys):
    """Schema-invalid workflow YAML must not block `git push`.

    ``load_workflow`` pipes the YAML through ``WorkflowConfig.model_validate``
    which raises ``pydantic.ValidationError`` for bad shapes. The runner has
    to catch that alongside ``FileNotFoundError`` / ``yaml.YAMLError`` so a
    malformed project workflow doesn't wedge the hook.
    """
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)
    _write_config_with_pre_push(project, None)
    (project / ".jig" / "workflows").mkdir()
    # Missing required ``name`` key → WorkflowConfig.model_validate raises
    # ValidationError. yaml.safe_load still succeeds on this input.
    (project / ".jig" / "workflows" / "default.yaml").write_text("phases: []\n")
    (project / ".jig" / "store").mkdir()
    # Block A.3 — Ticket carries ``extra="forbid"``; JSONL must not
    # duplicate ``id`` alongside ``_id`` (the alias).
    (project / ".jig" / "store" / "tickets.jsonl").write_text(
        '{"_op": "insert", "_id": "t-1", '
        '"work_type": "feature", "title": "x", "created_by": "u", '
        '"description": "## Acceptance criteria\\n- placeholder\\n", '
        '"workflow": "default"}\n'
    )

    wt = project / ".jig" / "worktrees" / "t-1"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {project / '.git' / 'worktrees' / 't-1'}\n")

    rc = asyncio.run(run_pre_push(wt))
    out = capsys.readouterr().out
    assert rc == 0
    assert "workflow 'default' not found; skipping" in out


def test_run_pre_push_in_worktree_ticket_missing_falls_through(tmp_path: Path):
    """Worktree path but no ticket record → behave as if outside a worktree."""
    project = tmp_path / "project"
    project.mkdir()
    _git_init(project)
    _write_config_with_pre_push(project, "true")  # fallback should fire

    wt = project / ".jig" / "worktrees" / "t-missing"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {project / '.git'}\n")

    rc = asyncio.run(run_pre_push(wt))
    assert rc == 0


# ---------------------------------------------------------------------------
# Task 12: commit-msg validator
# ---------------------------------------------------------------------------

from jig.hooks import run_commit_msg, validate_commit_msg  # noqa: E402


@pytest.mark.parametrize(
    "msg",
    [
        "feat: add hooks",
        "fix: correct typo",
        "fix(scope): correct typo",
        "chore!: breaking change",
        "perf(db): cache indexes",
        "build: bump deps",
        "ci: update workflow",
        "style: reformat",
        "revert: prior change",
        "docs: update readme",
        "test: cover edge case",
        "refactor: simplify",
    ],
)
def test_validate_commit_msg_accepts_valid(msg: str):
    assert validate_commit_msg(msg) is True


@pytest.mark.parametrize(
    "msg",
    [
        "random subject",
        "",
        "   leading whitespace: x",
        "feat add hooks",
        "FEAT: upper type",
        "feat:",
        "feat: ",
    ],
)
def test_validate_commit_msg_rejects_invalid(msg: str):
    assert validate_commit_msg(msg) is False


@pytest.mark.parametrize(
    "msg",
    [
        "Merge branch 'main'",
        'Revert "feat: add hooks"',
        "fixup! feat: add hooks",
        "squash! fix: typo",
    ],
)
def test_validate_commit_msg_autobypass_git_generated(msg: str):
    assert validate_commit_msg(msg) is True


def test_run_commit_msg_reads_file_and_returns_code(tmp_path: Path, capsys):
    msg_file = tmp_path / "MSG"
    msg_file.write_text("feat: ok\n")
    assert run_commit_msg(msg_file) == 0

    msg_file.write_text("nope\n")
    assert run_commit_msg(msg_file) == 1
    err = capsys.readouterr().err
    assert "nope" in err
    assert "conventional" in err.lower() or "expected" in err.lower()
