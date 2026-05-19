"""Stable per-ticket finding IDs.

Pure functions over a chronologically-ordered list of
``ReviewerComment`` rows. The store layer is responsible for handing
us a deterministic order
(:meth:`jig.store.review_comments.ReviewCommentsStore.for_ticket_chronological`);
this module turns that order into stable ``RC-N`` identifiers that
survive re-phrasing and additional cycles.

**Signature.** Two findings are "the same finding" when they share
``(reviewer, type, file, line)``. A reviewer that re-words the same
underlying concern across cycles produces the same signature and gets
the same ``RC-N``. A reviewer that pinpoints a different line gets a
new ID (intentional — the dev needs to ack each).

**ID assignment.** Walk the comments in insertion order. On first
appearance of a signature, assign the next ``RC-N``. On re-appearance,
reuse the existing ``RC-N``. Insertion order means new findings only
ever append higher IDs; prior acks against ``RC-3`` always point at the
same logical finding.

The signature collapses to ``(reviewer, type, None, None)`` when a
reviewer omits ``file`` / ``line`` — every diff-wide finding from a
(reviewer, type) pair collapses to one ID, matching the natural
interpretation ("this reviewer flagged a single category-level
concern").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jig.reviewers.comment import ReviewerComment

# (reviewer, type, file, line) — type and reviewer are the most
# stable discriminators; file+line pinpoint within a reviewer's domain.
FindingSignature = tuple[str, str, str | None, int | None]


def _signature(comment: "ReviewerComment") -> FindingSignature:
    # ``type`` may be enum or coerced str depending on validation path.
    type_value = (
        comment.type.value if hasattr(comment.type, "value") else str(comment.type)
    )
    return (comment.reviewer, type_value, comment.file, comment.line)


def compute_finding_ids(
    comments: "list[ReviewerComment]",
) -> dict[FindingSignature, str]:
    """Assign ``RC-N`` to each distinct signature in ``comments``.

    The caller must pass comments in canonical order (insertion /
    chronological). New signatures get the next sequential ID; repeats
    reuse the existing ID. The returned mapping covers every signature
    present in the input; lookup by signature is constant-time.
    """
    ids: dict[FindingSignature, str] = {}
    for comment in comments:
        sig = _signature(comment)
        if sig not in ids:
            ids[sig] = f"RC-{len(ids) + 1}"
    return ids


def find_by_id(
    comments: "list[ReviewerComment]",
    finding_id: str,
) -> "ReviewerComment | None":
    """Return the first comment whose signature maps to ``finding_id``.

    When a signature has multiple comments (re-phrased across cycles),
    we return the first by insertion order — typically the original
    raising. Returns ``None`` for unknown IDs.
    """
    ids = compute_finding_ids(comments)
    for comment in comments:
        if ids.get(_signature(comment)) == finding_id:
            return comment
    return None


__all__ = ["FindingSignature", "compute_finding_ids", "find_by_id"]
