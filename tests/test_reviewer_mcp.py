"""Tests for ``reviewer_mcp.handle_reviewer_post_comment`` (Track G MVP).

The handler accepts a ReviewerComment-shaped payload from a judgment
reviewer agent, validates, persists to the per-project ReviewCommentsStore,
and returns the new id. Covers happy path, role/ticket/cycle stamping,
and validation failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.persistence import load_role
from jig.reviewer_mcp import handle_reviewer_post_comment
from jig.store.review_comments import ReviewCommentsStore


class TestHandlePostComment:
    async def test_persists_and_returns_id(self, tmp_path: Path) -> None:
        cid = await handle_reviewer_post_comment(
            project_path=tmp_path,
            reviewer_role="reviewer-pattern-conformance",
            args={
                "type": "pattern-divergence",
                "severity": "important",
                "prose": (
                    "New module diverges from sibling pattern; nearby modules "
                    "use Pydantic models, this introduces a dataclass."
                ),
                "confidence": 0.7,
                "file": "jig/foo.py",
                "line": 12,
            },
            ticket_id="tkt-42",
            cycle=1,
        )
        assert cid

        store = ReviewCommentsStore(
            tmp_path / ".jig" / "store" / "review_comments.jsonl"
        )
        await store.load()
        got = await store.for_ticket("tkt-42")
        assert len(got) == 1
        c = got[0]
        assert c.reviewer == "reviewer-pattern-conformance"
        assert c.ticket_id == "tkt-42"
        assert c.cycle == 1
        assert c.confidence == 0.7

    async def test_payload_overrides_correlation_context(
        self, tmp_path: Path
    ) -> None:
        # Explicit ticket_id in args must beat the contextual stamp,
        # so the MVP+ "comment on a different ticket" use case stays open.
        cid = await handle_reviewer_post_comment(
            project_path=tmp_path,
            reviewer_role="reviewer-test-adequacy",
            args={
                "type": "test-adequacy",
                "severity": "critical",
                "prose": "no tests for new behavior",
                "confidence": 0.85,
                "ticket_id": "tkt-other",
            },
            ticket_id="tkt-self",
            cycle=2,
        )
        assert cid
        store = ReviewCommentsStore(
            tmp_path / ".jig" / "store" / "review_comments.jsonl"
        )
        await store.load()
        assert (await store.for_ticket("tkt-other"))
        assert not (await store.for_ticket("tkt-self"))

    async def test_invalid_payload_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            await handle_reviewer_post_comment(
                project_path=tmp_path,
                reviewer_role="reviewer-error-handling",
                args={
                    # missing required `severity`, `prose`
                    "type": "error-handling",
                },
            )


class TestRoleConfigsLoad:
    def test_pattern_conformance_loads(self, tmp_path: Path) -> None:
        cfg = load_role(tmp_path, "reviewer_pattern_conformance")
        assert cfg.role == "reviewer-pattern-conformance"
        assert "reviewer_post_comment" in cfg.allowed_tools
        assert cfg.strict_tools is True

    def test_error_handling_loads(self, tmp_path: Path) -> None:
        cfg = load_role(tmp_path, "reviewer_error_handling")
        assert cfg.role == "reviewer-error-handling"
        assert "reviewer_post_comment" in cfg.allowed_tools
        assert cfg.strict_tools is True

    def test_test_adequacy_loads(self, tmp_path: Path) -> None:
        cfg = load_role(tmp_path, "reviewer_test_adequacy")
        assert cfg.role == "reviewer-test-adequacy"
        assert "reviewer_post_comment" in cfg.allowed_tools
        assert cfg.strict_tools is True

    def test_test_adequacy_prompt_is_review_tests_phase_scoped(
        self, tmp_path: Path
    ) -> None:
        """The rewrite (feature-work/review-routing/plan.md §Step 3) reframes
        this reviewer around the new ``review-tests`` phase and the ticket
        AC — its prompt no longer cross-references impl-side callables
        (which don't exist at this phase)."""
        cfg = load_role(tmp_path, "reviewer_test_adequacy")
        prompt = cfg.phase_prompt
        # Phase context is explicit
        assert "review-tests" in prompt
        # AC-anchored mechanics replace the old impl-diff cross-reference
        assert "acceptance criteria" in prompt.lower() or "AC" in prompt
        # Explicit "no impl" guarantee — distinguishes this from end-of-ticket
        # reviewers that DO see impl
        assert "impl" in prompt.lower()

    def test_test_adequacy_drops_graph_consumers_of(self, tmp_path: Path) -> None:
        """``graph_consumers_of`` was used to inspect impl call-sites; the
        new pre-impl phase has no callers to inspect."""
        cfg = load_role(tmp_path, "reviewer_test_adequacy")
        assert "graph_consumers_of" not in cfg.allowed_tools

    def test_test_adequacy_default_context_keeps_ticket_description(
        self, tmp_path: Path
    ) -> None:
        """The AC-anchored mechanics depend on ``ticket://description`` —
        the prompt's reference must still be wired in default_context so
        the reviewer can read the AC at spawn time."""
        cfg = load_role(tmp_path, "reviewer_test_adequacy")
        assert "ticket://description" in cfg.default_context
