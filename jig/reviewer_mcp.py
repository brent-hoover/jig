"""MCP tool handler for judgment reviewers (Track G MVP follow-on).

The mechanical reviewers (contract-compliance, cross-cutting-policy,
spec-compliance) are deterministic Python that returns
``list[ReviewerComment]`` directly to the dispatcher. The judgment
reviewers (pattern-conformance, error-handling, test-adequacy) are
LLM-driven role configs — they read the diff, reason about it, and
emit comments through this MCP tool back into the
``ReviewCommentsStore`` per
``docs/pm-workflow/design.md`` §"Reviewer federation — selection logic".

One handler, one tool: ``reviewer_post_comment``. The agent posts one
comment per call (a single judgment-reviewer run can produce many).
The handler validates the payload as a ``ReviewerComment``, persists
to the store, and returns the assigned id so the agent can reference
it on subsequent calls (e.g. follow-up comments threaded on the same
file/line).

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
from jig.store.review_comments import ReviewCommentsStore


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
    """
    payload = dict(args)
    payload.setdefault("reviewer", reviewer_role)
    if "ticket_id" not in payload and ticket_id is not None:
        payload["ticket_id"] = ticket_id
    if "cycle" not in payload and cycle:
        payload["cycle"] = cycle

    try:
        comment = ReviewerComment.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            "reviewer_post_comment payload failed schema validation: "
            f"{exc.errors()}"
        ) from exc

    store_path = _store_path(project_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = ReviewCommentsStore(store_path)
    await store.load()
    return await store.append(comment)


__all__ = ["handle_reviewer_post_comment"]
