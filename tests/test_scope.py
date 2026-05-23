"""Tests for jig.scope — path_in_scope + glob_to_git_pathspec.

These functions are pure and shared by every layer of the reviewer
file-scoping feature: MCP tools (``reviewer_get_diff`` /
``reviewer_read_file``) and the routing-layer defence-in-depth.
Exhaustive coverage here means callers can trust the predicate
without re-verifying.
"""

from __future__ import annotations

import pytest

from jig.scope import glob_to_git_pathspec, path_in_scope


# ---------------------------------------------------------------------------
# path_in_scope
# ---------------------------------------------------------------------------


class TestPathInScopeIncludes:
    def test_single_pattern_match(self) -> None:
        assert path_in_scope("src/foo.py", include=["src/**"], exclude=[])

    def test_single_pattern_no_match(self) -> None:
        assert not path_in_scope(
            "tests/foo.py", include=["src/**"], exclude=[]
        )

    def test_multiple_patterns_any_match(self) -> None:
        # Any single include match is enough.
        assert path_in_scope(
            "pyproject.toml",
            include=["src/**", "pyproject.toml", "uv.lock"],
            exclude=[],
        )

    def test_empty_include_means_unscoped(self) -> None:
        """Empty include = no scoping → every non-empty path is in
        scope. Subject to excludes."""
        assert path_in_scope("any/path/here.py", include=[], exclude=[])
        assert path_in_scope("tests/test_x.py", include=[], exclude=[])

    def test_double_star_matches_arbitrary_depth(self) -> None:
        assert path_in_scope(
            "src/deep/nested/module/foo.py", include=["src/**"], exclude=[]
        )

    def test_single_star_matches_within_component(self) -> None:
        # ``*.py`` matches anything ending in .py at the ROOT only
        # under PurePosixPath.match semantics.
        assert path_in_scope("script.py", include=["*.py"], exclude=[])
        # NOT files in subdirectories.
        assert not path_in_scope(
            "src/foo.py", include=["*.py"], exclude=[]
        )

    def test_test_name_patterns(self) -> None:
        """The exact pattern set the test-adequacy reviewer uses to
        scope itself to test files."""
        include = [
            "tests/**",
            "**/conftest.py",
            "**/test_*.py",
            "**/*_test.py",
        ]
        assert path_in_scope("tests/test_foo.py", include=include, exclude=[])
        assert path_in_scope("conftest.py", include=include, exclude=[])
        assert path_in_scope(
            "src/conftest.py", include=include, exclude=[]
        )
        assert path_in_scope(
            "tests/integration/test_e2e.py", include=include, exclude=[]
        )
        assert path_in_scope(
            "src/foo_test.py", include=include, exclude=[]
        )
        # Non-test source file not in scope for the test reviewer.
        assert not path_in_scope(
            "src/foo.py", include=include, exclude=[]
        )


class TestPathInScopeExcludes:
    def test_exclude_wins_over_include(self) -> None:
        # The path matches the include pattern but the exclude
        # explicitly forbids it. Exclude wins.
        assert not path_in_scope(
            "tests/test_x.py",
            include=["**/*.py"],
            exclude=["tests/**"],
        )

    def test_exclude_does_not_affect_non_matching(self) -> None:
        assert path_in_scope(
            "src/foo.py",
            include=["src/**"],
            exclude=["tests/**"],
        )

    def test_multiple_excludes(self) -> None:
        excludes = ["tests/**", "**/conftest.py", "**/_test_*.py"]
        assert not path_in_scope(
            "tests/test_x.py", include=["**/*.py"], exclude=excludes
        )
        assert not path_in_scope(
            "src/conftest.py", include=["**/*.py"], exclude=excludes
        )

    def test_non_test_reviewer_scope(self) -> None:
        """The exact include/exclude set the non-test reviewers use.
        Asserts the high-value cases: src files in, tests out,
        config in."""
        include = [
            "src/**",
            "jig/**",
            "pyproject.toml",
            "uv.lock",
            "Dockerfile",
            "scripts/**",
            "docs/**",
        ]
        exclude = [
            "tests/**",
            "**/conftest.py",
            "**/test_*.py",
            "**/*_test.py",
        ]
        # Source — in.
        assert path_in_scope(
            "src/myproject/api.py", include=include, exclude=exclude
        )
        assert path_in_scope(
            "jig/orchestrator.py", include=include, exclude=exclude
        )
        # Config — in.
        assert path_in_scope(
            "pyproject.toml", include=include, exclude=exclude
        )
        assert path_in_scope("uv.lock", include=include, exclude=exclude)
        # Tests — out.
        assert not path_in_scope(
            "tests/test_api.py", include=include, exclude=exclude
        )
        assert not path_in_scope(
            "tests/integration/test_flow.py",
            include=include,
            exclude=exclude,
        )
        assert not path_in_scope(
            "conftest.py", include=include, exclude=exclude
        )
        # A test file named within src/ (rare but legal) — exclude
        # still wins.
        assert not path_in_scope(
            "src/conftest.py", include=include, exclude=exclude
        )
        # Outside any scope — out.
        assert not path_in_scope(
            "README.md", include=include, exclude=exclude
        )


class TestPathInScopeSafety:
    def test_empty_path_rejected(self) -> None:
        assert not path_in_scope("", include=["**"], exclude=[])

    def test_absolute_path_rejected(self) -> None:
        assert not path_in_scope(
            "/etc/passwd", include=["**"], exclude=[]
        )

    def test_path_traversal_rejected(self) -> None:
        assert not path_in_scope(
            "src/../etc/passwd", include=["**"], exclude=[]
        )
        assert not path_in_scope(
            "../outside/repo.py", include=["**"], exclude=[]
        )

    def test_dotdot_in_middle_rejected(self) -> None:
        assert not path_in_scope(
            "src/foo/../../etc/passwd",
            include=["src/**"],
            exclude=[],
        )

    @pytest.mark.parametrize("dangerous", [".", "/", "//", "..", "../"])
    def test_obviously_bad_paths(self, dangerous: str) -> None:
        assert not path_in_scope(dangerous, include=["**"], exclude=[])


# ---------------------------------------------------------------------------
# glob_to_git_pathspec
# ---------------------------------------------------------------------------


class TestGlobToGitPathspec:
    def test_empty_returns_empty(self) -> None:
        assert glob_to_git_pathspec([], []) == []

    def test_include_only(self) -> None:
        assert glob_to_git_pathspec(["src/**", "*.py"], []) == [
            "src/**",
            "*.py",
        ]

    def test_exclude_uses_git_pathspec_magic(self) -> None:
        # Git's exclusion pathspec magic is ``:(exclude)<glob>`` (or
        # the shorthand ``:!<glob>``). We emit the explicit form so
        # the intent is obvious in log lines.
        assert glob_to_git_pathspec(["src/**"], ["tests/**"]) == [
            "src/**",
            ":(exclude)tests/**",
        ]

    def test_includes_before_excludes(self) -> None:
        # Ordering matters for readability of generated `git diff`
        # command lines — includes first, then excludes.
        result = glob_to_git_pathspec(
            ["src/**", "pyproject.toml"],
            ["tests/**", "**/conftest.py"],
        )
        assert result == [
            "src/**",
            "pyproject.toml",
            ":(exclude)tests/**",
            ":(exclude)**/conftest.py",
        ]

    def test_excludes_only(self) -> None:
        """Edge case: caller provides only excludes. Git will treat
        every path as eligible and exclude the named ones. Useful for
        documenting what we DON'T want without enumerating the rest."""
        assert glob_to_git_pathspec([], ["tests/**"]) == [
            ":(exclude)tests/**",
        ]
