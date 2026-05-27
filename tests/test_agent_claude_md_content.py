"""Smoke tests for the shipped agent CLAUDE.md content.

These tests verify the SHIPPED files exist with expected content — they don't
exercise the injection mechanism (that's covered by test_agent_config.py and
test_worktree.py). The point is to catch drift early: if jig's MCP tool surface
gets renamed, these tests fail and surface the mismatch before agents start
referencing dead tool names.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.agent_config import _defaults_dir, _write_global_claude_md


class TestShippedGlobalContent:
    def test_global_file_exists_and_is_non_empty(self) -> None:
        global_md = _defaults_dir() / "agent_claude_md.md"
        assert global_md.is_file()
        text = global_md.read_text(encoding="utf-8")
        assert len(text) > 200, "global CLAUDE.md looks like a placeholder"

    @pytest.mark.parametrize(
        "tool_name",
        [
            # Ticket lifecycle
            "create_ticket",
            "read_ticket",
            "update_ticket",
            "list_tickets",
            # Conversation
            "comment_on_ticket",
            "read_comments",
            "thread_ask",
            "thread_answer",
            "thread_resolve_question",
            "thread_note",
            "thread_decide",
            "thread_handoff",
            "thread_accept_handoff",
            "thread_reject_handoff",
            # Working code
            "commit_progress",
            "add_dependency",
            # Knowledge
            "record_learning",
        ],
    )
    def test_global_mentions_known_mcp_tools(self, tool_name: str) -> None:
        """If a shipped MCP tool gets renamed, the global doc should call it
        out — this test fails when the doc still references the old name.

        Covers every MCP tool the global doc explicitly names. A rename in
        ``mcp_server.py`` without the corresponding doc update should trip
        this test; an unrelated tool added to the codebase is fine to omit
        until the doc actually mentions it."""
        global_md = _defaults_dir() / "agent_claude_md.md"
        text = global_md.read_text(encoding="utf-8")
        assert tool_name in text, (
            f"global CLAUDE.md missing reference to MCP tool {tool_name!r}"
        )

    def test_global_covers_required_sections(self) -> None:
        """Catches accidental wholesale gutting of the doc."""
        global_md = _defaults_dir() / "agent_claude_md.md"
        text = global_md.read_text(encoding="utf-8")
        for needle in (
            "Acceptance Criteria",
            "Commits",
            "Error handling",
            "Scope discipline",
        ):
            assert needle in text, (
                f"global CLAUDE.md missing expected section header {needle!r}"
            )


class TestShippedRoleAddendums:
    @pytest.mark.parametrize("role", ["dev", "reviewer-generalist"])
    def test_role_addendum_exists_and_is_non_empty(self, role: str) -> None:
        addendum = _defaults_dir() / "roles" / role / "CLAUDE.md"
        assert addendum.is_file(), (
            f"shipped per-role addendum missing for role {role!r}"
        )
        text = addendum.read_text(encoding="utf-8")
        assert len(text) > 200, f"addendum for {role!r} looks like a placeholder"


class TestRoleAddendumIntegration:
    """Exercise the actual concat behavior _write_global_claude_md performs
    on shipped files — catches missing addendum files or path-derivation bugs
    that the per-file existence tests above wouldn't notice."""

    def test_reviewer_generalist_concat_contains_both_sources(
        self, tmp_path: Path
    ) -> None:
        _write_global_claude_md(tmp_path, role="reviewer-generalist")
        text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "jig MCP server" in text  # from global
        assert "Role addendum" in text  # from reviewer-generalist addendum
        assert "Severity calibration" in text  # reviewer-generalist-specific

    def test_dev_concat_contains_both_sources(self, tmp_path: Path) -> None:
        _write_global_claude_md(tmp_path, role="dev")
        text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "jig MCP server" in text  # from global
        assert "Read tests first" in text  # dev-specific


class TestShippedTemplateStarters:
    @pytest.mark.parametrize(
        "template,marker",
        [
            ("python", "uv run pytest"),
            ("python-cli", "[project.scripts]"),
            ("fastapi", "uvicorn"),
        ],
    )
    def test_template_ships_starter_with_stack_marker(
        self, template: str, marker: str
    ) -> None:
        starter = (
            _defaults_dir() / "project_templates" / template / ".jig" / "CLAUDE.md"
        )
        assert starter.is_file(), f"{template} template missing .jig/CLAUDE.md starter"
        text = starter.read_text(encoding="utf-8")
        assert marker in text, (
            f"{template} starter missing expected stack marker {marker!r}"
        )
