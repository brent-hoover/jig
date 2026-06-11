"""Integration tests for `_apply_template_files`.

These tests run the actual template copy against a tmp dir to confirm:
1. Each shipped template copies through, including dotfiles and `.jig/` dirs.
2. The per-template `.jig/CLAUDE.md` starter (added by PR 2 of agent-claude-md)
   ends up at `<dest>/.jig/CLAUDE.md` with stack-specific content.

The existing `test_init_workflow.py` mocks `_apply_template_files` to keep its
focus on workflow orchestration; this file covers the function itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.init_workflow import _apply_template_files


@pytest.mark.parametrize(
    "template,stack_marker",
    [
        ("python", "uv run pytest"),
        ("python-cli", "[project.scripts]"),
        ("fastapi", "uvicorn"),
    ],
)
def test_apply_template_files_writes_jig_claude_md(
    template: str, stack_marker: str, tmp_path: Path
) -> None:
    """For every shipped template, scaffolding writes a `.jig/CLAUDE.md` to
    the destination with the expected stack-specific marker.

    Also implicitly confirms that `_apply_template_files`'s `rglob` traversal
    picks up files inside dotted-prefix directories (`.jig/`) — without that,
    the starter would silently get skipped at scaffold time."""
    dest = tmp_path / "scaffolded"
    dest.mkdir()

    _apply_template_files(
        template_name=template,
        dest=dest,
        project_name="example_project",
    )

    starter = dest / ".jig" / "CLAUDE.md"
    assert starter.is_file(), (
        f"{template} scaffold did not produce <dest>/.jig/CLAUDE.md"
    )
    text = starter.read_text(encoding="utf-8")
    assert stack_marker in text, (
        f"{template} starter at <dest> missing stack marker {stack_marker!r}"
    )


@pytest.mark.parametrize("template", ["python", "python-cli", "fastapi"])
def test_template_starter_uses_placeholder_not_literal_myproject(
    template: str,
) -> None:
    """Check the SOURCE `.jig/CLAUDE.md` directly, not the scaffolded
    output. `_apply_template_files` does a `myproject` → project-name
    substitution; if a starter contains literal `myproject`, the
    substitution silently rewrites it to whatever the user named their
    project and the doc diverges from what's checked in. We want the
    starter to use `<package>` as an explicit placeholder so its text
    survives substitution unchanged. Checking source rather than
    scaffold output makes the assertion meaningful — checking output
    would always pass because `myproject` would be gone by then either
    way."""
    from jig.agent_config import _defaults_dir

    starter = _defaults_dir() / "project_templates" / template / ".jig" / "CLAUDE.md"
    text = starter.read_text(encoding="utf-8")
    assert "myproject" not in text, (
        f"{template} starter contains literal 'myproject' — use "
        f"`<package>` placeholder so substitution doesn't silently "
        f"rewrite it"
    )


def test_apply_template_files_substitutes_project_name_in_pyproject(
    tmp_path: Path,
) -> None:
    """Sanity check that the substitution itself still works on a file
    where `myproject` IS the intended placeholder (pyproject.toml's
    `name = "myproject"` and `packages = ["src/myproject"]`)."""
    dest = tmp_path / "scaffolded"
    dest.mkdir()

    _apply_template_files(
        template_name="python",
        dest=dest,
        project_name="example_project",
    )

    pyproject_text = (dest / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "example_project"' in pyproject_text
    assert 'packages = ["src/example_project"]' in pyproject_text
    assert "myproject" not in pyproject_text


def test_hyphenated_project_name_two_token_substitution(tmp_path: Path) -> None:
    """A hyphenated project name yields a hyphenated dist/script name and
    an underscored package name.

    `myproject` (package contexts) → `hn_cli`; `my-project` (distribution
    contexts: [project] name, scripts key, README invocations) → `hn-cli`.
    Guards the eval tracer contract: the brief advertises `hn-cli`, so the
    scaffolded command must be `hn-cli`, not `hn_cli`.
    """
    dest = tmp_path / "scaffolded"
    dest.mkdir()

    _apply_template_files(
        template_name="python-cli",
        dest=dest,
        project_name="hn-cli",
    )

    pyproject = (dest / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "hn-cli"' in pyproject
    assert 'hn-cli = "hn_cli.cli:app"' in pyproject
    assert 'packages = ["src/hn_cli"]' in pyproject
    assert (dest / "src" / "hn_cli" / "cli.py").is_file()

    readme = (dest / "README.md").read_text(encoding="utf-8")
    assert "uv run hn-cli --help" in readme
    assert "python -m hn_cli" in readme

    # User-facing Typer help text uses the dist name, not the package name.
    cli_src = (dest / "src" / "hn_cli" / "cli.py").read_text(encoding="utf-8")
    assert 'help="hn-cli CLI."' in cli_src

    # No placeholder token survives substitution in any rendered text file.
    for f in dest.rglob("*"):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert "myproject" not in text, f"unsubstituted package token in {f}"
        assert "my-project" not in text, f"unsubstituted dist token in {f}"
