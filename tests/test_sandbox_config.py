"""Tests for ``jig.sandbox.BwrapConfig`` argument construction.

The bwrap argument list is the enforcement surface for agent
filesystem isolation. These tests assert the capability-policy
bind-mounts (Phase 5 Task G) appear with the right source and
destination paths, read-only, and are only present when the caller
declares them — a spawn with no capability policy should not see
``/jig/policy`` or ``/jig/bin`` mounted at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.sandbox import BwrapConfig


def _pair_positions(args: list[str], flag: str) -> list[tuple[str, str]]:
    """Extract (src, dst) pairs following every ``flag`` occurrence."""
    pairs: list[tuple[str, str]] = []
    for i, arg in enumerate(args):
        if arg == flag and i + 2 < len(args):
            pairs.append((args[i + 1], args[i + 2]))
    return pairs


class TestCapabilityMounts:
    def test_no_policy_no_extra_mounts(self, tmp_path: Path) -> None:
        """Spawn without capability declarations: no ``/jig/*`` mounts.

        The compiler doesn't write rules or hook registrations for
        such spawns — mounting empty policy artefacts would be
        enforcement theatre."""
        cfg = BwrapConfig(worktree_host_path=tmp_path)
        args = cfg.to_args()
        ro_binds = _pair_positions(args, "--ro-bind")
        destinations = [dst for _src, dst in ro_binds]
        assert "/jig/policy" not in destinations
        assert "/jig/bin" not in destinations

    def test_policy_and_hook_bin_mounted_readonly(self, tmp_path: Path) -> None:
        policy_dir = tmp_path / "policy"
        policy_dir.mkdir()
        hook_bin = tmp_path / "bin"
        hook_bin.mkdir()

        cfg = BwrapConfig(
            worktree_host_path=tmp_path,
            policy_dir_host_path=policy_dir,
            hook_bin_host_path=hook_bin,
        )
        args = cfg.to_args()

        ro_binds = _pair_positions(args, "--ro-bind")
        # Both mounts must be read-only — an agent that could rewrite
        # its own rules or hook scripts would have a sandbox escape.
        assert (str(policy_dir), "/jig/policy") in ro_binds
        assert (str(hook_bin), "/jig/bin") in ro_binds

        # Neither appears as --bind (rw) anywhere.
        rw_binds = _pair_positions(args, "--bind")
        rw_dsts = [dst for _src, dst in rw_binds]
        assert "/jig/policy" not in rw_dsts
        assert "/jig/bin" not in rw_dsts

    def test_hook_bin_mount_precedes_extras(self, tmp_path: Path) -> None:
        """Hook/policy mounts come before ``extra_ro_binds`` so callers
        can't accidentally shadow ``/jig/*`` by re-binding it."""
        policy_dir = tmp_path / "policy"
        policy_dir.mkdir()
        hook_bin = tmp_path / "bin"
        hook_bin.mkdir()
        extra_src = tmp_path / "extra"
        extra_src.mkdir()

        cfg = BwrapConfig(
            worktree_host_path=tmp_path,
            policy_dir_host_path=policy_dir,
            hook_bin_host_path=hook_bin,
            extra_ro_binds=[(str(extra_src), "/extra")],
        )
        args = cfg.to_args()

        def _first_index_of_dst(dst: str) -> int:
            for i, arg in enumerate(args):
                if arg == "--ro-bind" and i + 2 < len(args) and args[i + 2] == dst:
                    return i
            raise AssertionError(f"{dst!r} not found among --ro-bind args")

        # hook_bin/policy registered before the caller's extra binds.
        assert _first_index_of_dst("/jig/bin") < _first_index_of_dst("/extra")
        assert _first_index_of_dst("/jig/policy") < _first_index_of_dst("/extra")

    def test_policy_without_hook_bin_still_mounts(self, tmp_path: Path) -> None:
        """The two paths are independent — setting one without the
        other is a misconfiguration but ``BwrapConfig`` doesn't enforce
        pairing. That's the caller's job (``agent.py`` sets both or
        neither). We only check the argument builder honours each
        field individually."""
        policy_dir = tmp_path / "policy"
        policy_dir.mkdir()

        cfg = BwrapConfig(
            worktree_host_path=tmp_path,
            policy_dir_host_path=policy_dir,
        )
        args = cfg.to_args()
        ro_binds = _pair_positions(args, "--ro-bind")
        assert (str(policy_dir), "/jig/policy") in ro_binds
        dsts = [dst for _src, dst in ro_binds]
        assert "/jig/bin" not in dsts


class TestReservedMountProtection:
    """Extra mounts must not target (or overlap) ``/jig/bin`` or
    ``/jig/policy``. Argument ordering alone can't enforce this —
    bwrap honours later binds, so a later ``--bind`` pointing at
    ``/jig/bin`` would overwrite the read-only enforcement mount.
    :meth:`BwrapConfig.__post_init__` rejects this at config time."""

    @pytest.mark.parametrize(
        "dst",
        [
            "/jig/bin",
            "/jig/bin/",
            "/jig/bin/check-bash",
            "/jig/policy",
            "/jig/policy/rules.json",
            # Ancestor — ``/jig`` shadows both reserved mounts.
            "/jig",
            # Root shadows everything.
            "/",
        ],
    )
    def test_extra_ro_bind_overlap_rejected(self, tmp_path: Path, dst: str) -> None:
        with pytest.raises(ValueError, match="reserved mount"):
            BwrapConfig(
                worktree_host_path=tmp_path,
                extra_ro_binds=[(str(tmp_path), dst)],
            )

    def test_extra_rw_bind_overlap_rejected(self, tmp_path: Path) -> None:
        # Writable overlap is the dangerous case — it would let the
        # agent re-home the enforcement binaries onto a writable
        # directory.
        with pytest.raises(ValueError, match="extra_rw_binds"):
            BwrapConfig(
                worktree_host_path=tmp_path,
                extra_rw_binds=[(str(tmp_path), "/jig/bin")],
            )

    def test_hide_paths_overlap_rejected(self, tmp_path: Path) -> None:
        # A ``hide_paths`` entry overlays tmpfs — would empty out the
        # policy/hook directory from the agent's view.
        with pytest.raises(ValueError, match="hide_paths"):
            BwrapConfig(
                worktree_host_path=tmp_path,
                hide_paths=["/jig/policy"],
            )

    def test_sibling_destination_allowed(self, tmp_path: Path) -> None:
        """``/jig/binned`` shares a parent with ``/jig/bin`` but is a
        sibling, not an ancestor/descendant — segment-aware comparison
        lets it through."""
        cfg = BwrapConfig(
            worktree_host_path=tmp_path,
            extra_ro_binds=[(str(tmp_path), "/jig/binned")],
        )
        # Also double-check the arg builder still emits it.
        args = cfg.to_args()
        assert (str(tmp_path), "/jig/binned") in _pair_positions(args, "--ro-bind")

    def test_unrelated_destination_allowed(self, tmp_path: Path) -> None:
        """Mounts into unrelated subtrees stay unaffected by the guard."""
        # Use a tmp-rooted path for hide_paths: BwrapConfig.to_args() calls
        # Path(p).is_file() on each hide path, and on hosts where '/root'
        # exists with restricted permissions (Ubuntu CI runners) that stat
        # raises PermissionError. Sticking inside tmp_path keeps the test
        # OS-independent.
        cfg = BwrapConfig(
            worktree_host_path=tmp_path,
            extra_ro_binds=[(str(tmp_path), "/opt/data")],
            extra_rw_binds=[(str(tmp_path), "/var/cache/jig")],
            hide_paths=[str(tmp_path / "fake-hidden")],
        )
        args = cfg.to_args()
        assert (str(tmp_path), "/opt/data") in _pair_positions(args, "--ro-bind")
        assert (str(tmp_path), "/var/cache/jig") in _pair_positions(args, "--bind")


class TestEnvIsolation:
    """SEC-2: bwrap clears the orchestrator's environment and only
    forwards a curated allowlist via ``--setenv``. ``CLAUDE_CODE_OAUTH_TOKEN``
    IS in the allowlist so the bundled claude CLI can authenticate inside
    bwrap (the mounted ``~/.claude.json`` carries only profile info —
    credentials live in the host keychain, which the sandbox can't
    reach). ``ANTHROPIC_API_KEY`` is NOT in the allowlist: agents
    authenticate via OAuth, not the API key."""

    def test_clearenv_first(self, tmp_path: Path) -> None:
        cfg = BwrapConfig(worktree_host_path=tmp_path)
        args = cfg.to_args()
        assert args[0] == "--clearenv", "--clearenv must be the first arg"

    def test_default_passthrough_includes_oauth_excludes_api_key(
        self, tmp_path: Path
    ) -> None:
        cfg = BwrapConfig(worktree_host_path=tmp_path)
        # OAuth token forwarded so the bundled claude CLI can authenticate
        # inside bwrap. See jig/sandbox.py:65-73 for the rationale.
        assert "CLAUDE_CODE_OAUTH_TOKEN" in cfg.passthrough_env_keys
        # API key is NOT forwarded — agents use OAuth.
        assert "ANTHROPIC_API_KEY" not in cfg.passthrough_env_keys

    def test_passthrough_emitted_as_setenv(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
        monkeypatch.setenv("HOME", "/home/jig")
        # In the allowlist — must appear in --setenv when set on the host.
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth")
        # Outside the allowlist — must NOT appear in --setenv.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        cfg = BwrapConfig(worktree_host_path=tmp_path)
        args = cfg.to_args()
        setenv_pairs = _pair_positions(args, "--setenv")
        keys = {k for k, _v in setenv_pairs}
        assert ("PATH", "/usr/local/bin:/usr/bin") in setenv_pairs
        assert ("HOME", "/home/jig") in setenv_pairs
        assert ("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth") in setenv_pairs
        assert "ANTHROPIC_API_KEY" not in keys

    def test_extra_setenv_emitted(self, tmp_path: Path) -> None:
        cfg = BwrapConfig(
            worktree_host_path=tmp_path,
            extra_setenv=(("JIG_DEV_DB_URL", "postgres://..."),),
        )
        args = cfg.to_args()
        setenv_pairs = _pair_positions(args, "--setenv")
        assert ("JIG_DEV_DB_URL", "postgres://...") in setenv_pairs


class TestClaudeHomeReadOnly:
    """SEC-3: the Claude config and credentials are bound read-only so
    a Bash-capable agent can't tamper with the orchestrator's tokens
    or plugins."""

    def test_claude_dir_bound_readonly_when_present(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        claude_dir = fake_home / ".claude"
        claude_dir.mkdir()
        claude_json = fake_home / ".claude.json"
        claude_json.write_text("{}")
        monkeypatch.setenv("HOME", str(fake_home))

        cfg = BwrapConfig(worktree_host_path=tmp_path)
        args = cfg.to_args()

        ro_binds = _pair_positions(args, "--ro-bind")
        rw_binds = _pair_positions(args, "--bind")
        assert (str(claude_dir), str(claude_dir)) in ro_binds
        assert (str(claude_json), str(claude_json)) in ro_binds
        # Must NOT appear as rw --bind.
        rw_dsts = [dst for _src, dst in rw_binds]
        assert str(claude_dir) not in rw_dsts
        assert str(claude_json) not in rw_dsts


class TestProjectHidden:
    """SEC-4: callers can hide the orchestrator's project mount so the
    agent's filesystem view narrows toward "only its worktree". The
    worktree itself is bind-mounted independently and stays visible."""

    def test_hide_project_does_not_block_worktree_bind(self, tmp_path: Path) -> None:
        cfg = BwrapConfig(
            worktree_host_path=tmp_path,
            hide_paths=["/project"],
        )
        args = cfg.to_args()
        # Worktree bind to /workspace is still emitted.
        rw_binds = _pair_positions(args, "--bind")
        assert (str(tmp_path), "/workspace") in rw_binds
        # /project is overlaid with tmpfs.
        tmpfs_targets = [
            args[i + 1]
            for i, a in enumerate(args)
            if a == "--tmpfs" and i + 1 < len(args)
        ]
        assert "/project" in tmpfs_targets
