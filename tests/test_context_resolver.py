"""Tests for jig.context_resolver (Phase 2 Tasks A + B).

Covers every scheme (``project://``, ``role://``, ``ticket://``,
``decision://``, ``repo://``), the deprecated ``issue://`` alias,
required vs optional (``strict=True``) semantics, and the "not
resolved" vs "resolved but empty" distinction enforced by the private
``_resolve_one`` helper.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from jig.context_resolver import (
    MissingContextError,
    resolve_context_uri,
    resolve_context_uris,
)
from jig.store.comments import CommentStore
from jig.ticket import Comment, Ticket, WorkType


@pytest.fixture
def jig_layout(tmp_path: Path) -> Path:
    """Project + worktree root with the expected directory layout."""
    for sub in (
        ".jig/context/project",
        ".jig/context/roles/dev",
        ".jig/decisions",
    ):
        (tmp_path / sub).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def ticket() -> Ticket:
    return Ticket(
        id="T-001",
        work_type=WorkType.FEATURE,
        title="Add widget",
        description="Build the widget.",
        created_by="tester",
    )


@pytest.fixture
async def comments(tmp_path: Path) -> CommentStore:
    store = CommentStore(tmp_path / "comments.jsonl")
    await store.load()
    return store


async def _resolve(
    uris: list[str],
    *,
    jig_layout: Path,
    ticket: Ticket,
    comments: CommentStore,
    strict: bool = False,
) -> str:
    return await resolve_context_uris(
        uris,
        ticket=ticket,
        parent=None,
        comments=comments,
        worktree_path=jig_layout,
        project_path=jig_layout,
        strict=strict,
    )


# ---- project:// -----------------------------------------------------------


class TestProjectScheme:
    async def test_resolves_extant_file(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        (jig_layout / ".jig" / "context" / "project" / "principles.md").write_text(
            "Keep it simple."
        )
        out = await _resolve(
            ["project://principles.md"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert "Keep it simple." in out

    async def test_md_suffix_fallback(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        (jig_layout / ".jig" / "context" / "project" / "principles.md").write_text(
            "Ship"
        )
        out = await _resolve(
            ["project://principles"],  # no extension — resolver tries .md
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert "Ship" in out

    async def test_missing_file_returns_empty(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        out = await _resolve(
            ["project://nope.md"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert out == ""

    async def test_missing_file_is_strict_error(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        with pytest.raises(MissingContextError) as ei:
            await _resolve(
                ["project://nope.md"],
                jig_layout=jig_layout,
                ticket=ticket,
                comments=comments,
                strict=True,
            )
        assert ei.value.uri == "project://nope.md"


# ---- role:// --------------------------------------------------------------


class TestRoleScheme:
    async def test_resolves(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        (jig_layout / ".jig" / "context" / "roles" / "dev" / "style.md").write_text(
            "4-space indent."
        )
        out = await _resolve(
            ["role://dev/style.md"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert "4-space indent." in out

    async def test_malformed_returns_none(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        # role:// without a path component
        out = await _resolve(
            ["role://dev"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert out == ""

    async def test_malformed_is_strict_error(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        with pytest.raises(MissingContextError):
            await _resolve(
                ["role://dev"],
                jig_layout=jig_layout,
                ticket=ticket,
                comments=comments,
                strict=True,
            )


# ---- ticket:// ------------------------------------------------------------


class TestTicketScheme:
    async def test_description(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        out = await _resolve(
            ["ticket://description"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert "Build the widget." in out

    async def test_thread_concatenates_comments(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        await comments.post(
            Comment(
                ticket_id=ticket.id,
                author="alice",
                content="Q: shape?",
                kind="question",
            )
        )
        await comments.post(
            Comment(
                ticket_id=ticket.id,
                author="bob",
                content="A: circle",
                kind="answer",
            )
        )
        out = await _resolve(
            ["ticket://thread"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert "alice" in out and "bob" in out
        assert "Q: shape?" in out and "A: circle" in out
        # Chronological: alice's entry appears first.
        assert out.index("alice") < out.index("bob")

    async def test_unknown_artifact_is_empty(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        out = await _resolve(
            ["ticket://bogus"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert out == ""


# ---- decision:// ----------------------------------------------------------


class TestDecisionScheme:
    async def test_resolves_with_and_without_extension(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        (jig_layout / ".jig" / "decisions" / "DR-0001.md").write_text(
            "We picked pg."
        )
        bare = await _resolve(
            ["decision://DR-0001"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        suffixed = await _resolve(
            ["decision://DR-0001.md"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert "We picked pg." in bare
        assert "We picked pg." in suffixed


# ---- repo:// --------------------------------------------------------------


class TestRepoScheme:
    async def test_resolves_relative_to_worktree(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        (jig_layout / "README.md").write_text("hello readme")
        out = await _resolve(
            ["repo://README.md"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert "hello readme" in out

    async def test_missing_file_returns_empty(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        out = await _resolve(
            ["repo://nope.py"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert out == ""


# ---- issue:// alias -------------------------------------------------------


class TestIssueAlias:
    async def test_issue_resolves_as_ticket(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        out = await _resolve(
            ["issue://description"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert "Build the widget." in out

    async def test_issue_logs_deprecation_warning(
        self,
        jig_layout: Path,
        ticket: Ticket,
        comments: CommentStore,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Reset the once-per-process flag so this test is deterministic
        # regardless of earlier tests in the same session.
        import jig.context_resolver as cr

        monkeypatch.setattr(cr, "_issue_alias_warned", False)
        caplog.set_level(logging.WARNING, logger=cr.__name__)

        await _resolve(
            ["issue://description"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "issue://" in joined and "deprecated" in joined


# ---- required vs optional / strict ---------------------------------------


class TestStrictMode:
    async def test_strict_raises_on_first_miss(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        with pytest.raises(MissingContextError):
            await _resolve(
                ["project://missing.md"],
                jig_layout=jig_layout,
                ticket=ticket,
                comments=comments,
                strict=True,
            )

    async def test_non_strict_skips_misses(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        (jig_layout / ".jig" / "context" / "project" / "ok.md").write_text("ok")
        out = await _resolve(
            ["project://missing.md", "project://ok.md"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        # The missing one is silently skipped, the present one is included.
        assert "ok" in out

    async def test_strict_ok_when_all_resolve(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        (jig_layout / ".jig" / "context" / "project" / "ok.md").write_text("ok")
        out = await _resolve(
            ["project://ok.md"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
            strict=True,
        )
        assert "ok" in out

    async def test_unknown_scheme_non_strict_skips(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        out = await _resolve(
            ["weird://thing"],
            jig_layout=jig_layout,
            ticket=ticket,
            comments=comments,
        )
        assert out == ""

    async def test_unknown_scheme_strict_raises(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        with pytest.raises(MissingContextError):
            await _resolve(
                ["weird://thing"],
                jig_layout=jig_layout,
                ticket=ticket,
                comments=comments,
                strict=True,
            )


# ---- single-URI wrapper ---------------------------------------------------


class TestResolveContextUri:
    async def test_returns_none_when_missing(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        out = await resolve_context_uri(
            "project://missing.md",
            ticket=ticket,
            parent=None,
            comments=comments,
            worktree_path=jig_layout,
            project_path=jig_layout,
        )
        assert out is None

    async def test_returns_text_when_present(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        (jig_layout / ".jig" / "context" / "project" / "p.md").write_text("hello")
        out = await resolve_context_uri(
            "project://p.md",
            ticket=ticket,
            parent=None,
            comments=comments,
            worktree_path=jig_layout,
            project_path=jig_layout,
        )
        assert out is not None and "hello" in out

    async def test_missing_scheme_returns_none(
        self, jig_layout: Path, ticket: Ticket, comments: CommentStore
    ) -> None:
        out = await resolve_context_uri(
            "no-scheme-here",
            ticket=ticket,
            parent=None,
            comments=comments,
            worktree_path=jig_layout,
            project_path=jig_layout,
        )
        assert out is None
