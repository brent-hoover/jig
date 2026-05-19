"""MCP tool handler for judgment reviewers (Track G MVP follow-on).

The mechanical reviewers (contract-compliance, cross-cutting-policy,
spec-compliance) are deterministic Python that returns
``list[ReviewerComment]`` directly to the dispatcher. The judgment
reviewers (pattern-conformance, error-handling, test-adequacy) are
LLM-driven role configs — they read the diff, reason about it, and
emit comments through this MCP tool back into the
``ReviewCommentsStore`` per
``docs/v2.0/pm-workflow/design.md`` §"Reviewer federation — selection logic".

One handler, one tool: ``reviewer_post_comment``. The agent posts one
comment per call (a single judgment-reviewer run can produce many).
The handler validates the payload as a ``ReviewerComment``, runs the
self-check gate (Track G Final, see ``jig.reviewers.self_check``), and
on pass persists to the store and returns the assigned id. On a
self-check drop, the handler raises ``SelfCheckDropped`` so the
reviewer agent's tool call gets a clear "this got filtered" signal it
can incorporate into its next decision.

Why one tool instead of "post a batch"? The Claude Agent SDK reports
each tool call individually for tracing / cost attribution; one-comment-
per-call gives operators per-finding visibility in the analytics
``ToolCalled`` stream. Batches would compress that signal and make the
ReviewCommentPosted analytics correlation noisier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jig.reviewers.comment import ReviewerComment
from jig.reviewers.self_check import (
    SelfCheckResult,
    validate_comment_for_self_check,
)
from jig.store.review_comments import ReviewCommentsStore


class SelfCheckDropped(ValueError):
    """Raised when ``handle_reviewer_post_comment`` drops a comment.

    Subclasses ``ValueError`` so existing callers that catch validation
    failures keep working; the ``result`` attribute carries the
    structured ``SelfCheckResult`` so analytics / tests can introspect.
    """

    def __init__(self, result: SelfCheckResult) -> None:
        self.result = result
        super().__init__(
            f"reviewer_post_comment dropped by self-check: {result.reason}"
        )


def _store_path(project_path: Path) -> Path:
    """Match the orchestrator's per-project store layout convention."""
    return project_path / ".jig" / "store" / "review_comments.jsonl"


async def handle_reviewer_post_comment(
    *,
    project_path: Path,
    reviewer_role: str,
    args: dict[str, Any],
    ticket_id: str | None = None,
    cycle: int = 0,
) -> str:
    """Persist one judgment-reviewer comment to the store.

    ``reviewer_role`` is the agent role at the call site (e.g.
    ``reviewer-pattern-conformance``); the handler stamps it onto the
    comment when the payload omits ``reviewer`` so the structured-comment
    schema's ``reviewer`` field is always populated.

    ``ticket_id`` and ``cycle`` come from the wrapping MCP factory's
    correlation context (per-agent ticket scope, current cycle number).
    The agent's payload may include them too — we prefer the explicit
    payload when both are present so the LLM can target a different
    ticket when the operator's prompt asks for it (rare, but supported
    for the future cross-ticket pattern-conformance pass).

    The deterministic self-check gate (Track G Final) runs after schema
    validation but before persistence. A dropped comment raises
    ``SelfCheckDropped`` carrying the structured ``SelfCheckResult`` so
    the reviewer agent can refine and retry — operator never sees
    low-signal noise.
    """
    payload = dict(args)
    payload.setdefault("reviewer", reviewer_role)
    if "ticket_id" not in payload and ticket_id is not None:
        payload["ticket_id"] = ticket_id
    # The orchestrator's spawn-time cycle is authoritative. Agents
    # cannot override it — a reviewer that hallucinated a stale or
    # made-up cycle would corrupt latest-cycle routing and reraised
    # detection. Always overwrite when the caller (the MCP factory)
    # supplies a cycle, regardless of what the agent's payload says.
    if cycle:
        payload["cycle"] = cycle

    try:
        comment = ReviewerComment.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"reviewer_post_comment payload failed schema validation: {exc.errors()}"
        ) from exc

    self_check = validate_comment_for_self_check(comment)
    if not self_check.should_post:
        raise SelfCheckDropped(self_check)

    store_path = _store_path(project_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = ReviewCommentsStore(store_path)
    await store.load()
    return await store.append(comment)


__all__ = ["SelfCheckDropped", "handle_reviewer_post_comment"]
