"""Unit tests for ``jig.bin._hooklib``.

The shared helper module backs the ``check-bash``, ``check-write``,
``check-path`` scripts (Phase 5 Task G, doc 16). These tests exercise
its three exposed primitives — glob matching, URI resolution, path
normalisation — plus the HookPayload / rules-file loaders. End-to-end
coverage of each script as an executable lives in
``test_hook_scripts.py``.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest


def _load_hooklib():
    """Import ``_hooklib`` from its on-disk location.

    ``jig.bin`` ships ``_hooklib.py`` alongside three no-extension
    executable scripts. The parent ``jig/bin/__init__.py`` has no
    re-exports, so we load the module by path rather than by dotted
    name — this keeps the test aligned with how the scripts actually
    find the helper at runtime (same-directory ``sys.path`` insertion)."""

    from jig.bin import hook_bin_dir

    hooklib_path = hook_bin_dir() / "_hooklib.py"
    spec = importlib.util.spec_from_file_location("_hooklib", hooklib_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def hooklib():
    return _load_hooklib()


class TestMatchGlob:
    """Segment-wise glob matcher with ``**`` semantics."""

    @pytest.mark.parametrize(
        "pattern, path",
        [
            ("/workspace/foo.py", "/workspace/foo.py"),
            ("/workspace/*.py", "/workspace/foo.py"),
            ("/workspace/**", "/workspace/foo.py"),
            ("/workspace/**", "/workspace/a/b/c.py"),
            ("/workspace/**/*.py", "/workspace/a/b/c.py"),
            ("/workspace/**/*.py", "/workspace/c.py"),
            ("/workspace/a/**/c.py", "/workspace/a/b/c.py"),
            ("/workspace/a/**/c.py", "/workspace/a/c.py"),
            ("/a", "/a"),
            ("/a/b?.py", "/a/b1.py"),
        ],
    )
    def test_matches(self, hooklib, pattern: str, path: str) -> None:
        assert hooklib.match_glob(pattern, path)

    @pytest.mark.parametrize(
        "pattern, path",
        [
            # ``*`` does not cross segment boundaries.
            ("/workspace/*.py", "/workspace/a/b.py"),
            # ``**`` needs at least the trailing segment.
            ("/workspace/**/*.py", "/workspace/a/b.txt"),
            # Literal mismatch.
            ("/workspace/foo.py", "/workspace/bar.py"),
            # ``?`` is exactly one char.
            ("/a/b?.py", "/a/b.py"),
        ],
    )
    def test_non_matches(self, hooklib, pattern: str, path: str) -> None:
        assert not hooklib.match_glob(pattern, path)

    def test_trailing_slash_tolerated(self, hooklib) -> None:
        assert hooklib.match_glob("/workspace/", "/workspace")
        assert hooklib.match_glob("/workspace", "/workspace/")


class TestResolveURIGlob:
    """URI schemes → sandbox-absolute paths."""

    def test_ticket_worktree_prefix_resolves(self, hooklib) -> None:
        assert hooklib.resolve_uri_glob("ticket://worktree/**") == "/workspace/**"
        assert (
            hooklib.resolve_uri_glob("ticket://worktree/src/foo.py")
            == "/workspace/src/foo.py"
        )

    def test_repo_prefix_resolves_to_workspace(self, hooklib) -> None:
        # repo:// == worktree in the sandbox — same bind point.
        assert hooklib.resolve_uri_glob("repo://src/**") == "/workspace/src/**"

    def test_absolute_path_passthrough(self, hooklib) -> None:
        assert hooklib.resolve_uri_glob("/workspace/build/**") == "/workspace/build/**"

    @pytest.mark.parametrize(
        "pattern",
        [
            # These URI schemes are valid in the catalog but have no
            # concrete sandbox mount — hook treats them as non-matching.
            "project://brief/**",
            "role://dev/**",
            "decision://**",
            "issue://123/**",
            "ticket://spec/**",
            "ticket://design/**",
        ],
    )
    def test_unmapped_schemes_return_none(self, hooklib, pattern: str) -> None:
        assert hooklib.resolve_uri_glob(pattern) is None

    def test_schemeless_relative_returns_none(self, hooklib) -> None:
        # Catalog validation rejects these; defensively the hook
        # treats them as non-matching rather than crashing.
        assert hooklib.resolve_uri_glob("src/**") is None


class TestNormalizePath:
    def test_absolute_path_returned_unchanged(self, hooklib) -> None:
        assert hooklib.normalize_path("/workspace/a/b.py") == "/workspace/a/b.py"

    def test_relative_path_resolved_against_cwd(self, hooklib) -> None:
        # Agents operate from /workspace in the sandbox.
        assert hooklib.normalize_path("src/foo.py") == "/workspace/src/foo.py"

    def test_dotdot_collapsed_lexically(self, hooklib) -> None:
        # normpath eats ``..`` — combined with bwrap's filesystem scope
        # this is enough to make the string-compare safe.
        assert hooklib.normalize_path("/workspace/a/../b.py") == "/workspace/b.py"

    def test_empty_path_raises(self, hooklib) -> None:
        with pytest.raises(hooklib.HookError):
            hooklib.normalize_path("")


class TestDeniesPath:
    def test_first_matching_pattern_wins(self, hooklib) -> None:
        hit = hooklib.denies_path(
            "/workspace/.jig/spec/project.yaml",
            ["ticket://worktree/build/**", "ticket://worktree/.jig/spec/**"],
        )
        assert hit == "ticket://worktree/.jig/spec/**"

    def test_no_match_returns_none(self, hooklib) -> None:
        hit = hooklib.denies_path(
            "/workspace/src/foo.py",
            ["ticket://worktree/.jig/spec/**"],
        )
        assert hit is None

    def test_unresolvable_patterns_skipped(self, hooklib) -> None:
        # ``project://`` has no sandbox mapping, so it cannot match
        # anything — the deny helper just skips it.
        hit = hooklib.denies_path(
            "/workspace/src/foo.py",
            ["project://brief/**", "ticket://worktree/src/**"],
        )
        assert hit == "ticket://worktree/src/**"


class TestWritablePermitsPath:
    def test_any_match_permits(self, hooklib) -> None:
        assert hooklib.writable_permits_path(
            "/workspace/build/out.js",
            ["ticket://worktree/docs/**", "ticket://worktree/build/**"],
        )

    def test_no_match_denies(self, hooklib) -> None:
        assert not hooklib.writable_permits_path(
            "/workspace/.jig/spec/x.yaml",
            ["ticket://worktree/src/**"],
        )


class TestReadPayload:
    def test_parses_tool_name_and_input(self, hooklib) -> None:
        payload = hooklib.read_payload(
            io.StringIO(
                json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
            )
        )
        assert payload.tool_name == "Bash"
        assert payload.tool_input == {"command": "ls"}

    def test_missing_tool_name_raises(self, hooklib) -> None:
        with pytest.raises(hooklib.HookError, match="tool_name"):
            hooklib.read_payload(io.StringIO(json.dumps({"tool_input": {}})))

    def test_non_object_tool_input_raises(self, hooklib) -> None:
        with pytest.raises(hooklib.HookError, match="tool_input"):
            hooklib.read_payload(
                io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": "ls"}))
            )

    def test_invalid_json_raises(self, hooklib) -> None:
        with pytest.raises(hooklib.HookError, match="valid JSON"):
            hooklib.read_payload(io.StringIO("not json"))


class TestLoadRules:
    def test_reads_json_file(self, hooklib, tmp_path: Path) -> None:
        rules_path = tmp_path / "rules.json"
        rules_path.write_text(json.dumps({"bash": {"deny_patterns": ["rm -rf"]}}))
        loaded = hooklib.load_rules(rules_path)
        assert loaded == {"bash": {"deny_patterns": ["rm -rf"]}}

    def test_missing_file_raises(self, hooklib, tmp_path: Path) -> None:
        with pytest.raises(hooklib.HookError, match="cannot read"):
            hooklib.load_rules(tmp_path / "does-not-exist.json")

    def test_invalid_json_raises(self, hooklib, tmp_path: Path) -> None:
        rules_path = tmp_path / "rules.json"
        rules_path.write_text("{not valid")
        with pytest.raises(hooklib.HookError, match="valid JSON"):
            hooklib.load_rules(rules_path)
