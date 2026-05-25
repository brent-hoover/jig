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


def test_apply_template_files_substitutes_project_name(tmp_path: Path) -> None:
    """Sanity: the per-template starter doesn't accidentally introduce a
    `myproject` string that should have been substituted away. The template
    copy already runs the `myproject` -> `<package>` substitution; any new
    file added to the template tree needs to either use the placeholder or
    be substitution-stable."""
    dest = tmp_path / "scaffolded"
    dest.mkdir()

    _apply_template_files(
        template_name="python",
        dest=dest,
        project_name="example_project",
    )

    starter_text = (dest / ".jig" / "CLAUDE.md").read_text(encoding="utf-8")
    # The shipped starter uses `<package>` as a placeholder, not `myproject`,
    # so this should never trip — but if a future edit accidentally writes
    # `myproject` literally, the substitution would silently rewrite it to
    # `example_project` here and downstream content would diverge from
    # what's checked in.
    assert "myproject" not in starter_text
