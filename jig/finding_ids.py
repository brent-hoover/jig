"""Stable per-ticket finding IDs.

Pure functions over a chronologically-ordered list of
``ReviewerComment`` rows. The store layer is responsible for handing
us a deterministic order
(:meth:`jig.store.review_comments.ReviewCommentsStore.for_ticket_chronological`);
this module turns that order into stable ``RC-N`` identifiers that
survive re-phrasing and additional cycles.

**Signature.** Two findings are "the same finding" when they share
``(reviewer, type, file, contract_uri_norm)``. A reviewer that re-words
the same underlying concern across cycles produces the same signature
and gets the same ``RC-N``. ``contract_uri`` carries the AC identity —
the most semantically stable discriminator we have, and the one that
keeps the ID attached across cycles where the file has grown and the
reviewer's insertion-point line has moved.

**Line is NOT in the signature.** The reviewer's ``line`` field is an
aid for the dev to locate code, not an identifier. Including it broke
cross-cycle continuity: once the test file grew the reviewer would
point at a new "where to insert the test" line, mint a fresh signature,
and the dev's prior ack would silently detach. It also broke
within-cycle distinction: a reviewer that posts three findings at the
same insertion-point line (different AC items at the end of the file)
would have all three collapse into one RC, the dev would ack only the
one it understood, and the others would silently re-surface next cycle.

**ID assignment.** Walk the comments in insertion order. On first
appearance of a signature, assign the next ``RC-N``. On re-appearance,
reuse the existing ``RC-N``. Insertion order means new findings only
ever append higher IDs; prior acks against ``RC-3`` always point at the
same logical finding.

The signature collapses to ``(reviewer, type, None, None)`` when a
reviewer omits both ``file`` and ``contract_uri`` — every diff-wide
finding from a (reviewer, type) pair collapses to one ID, matching the
natural interpretation ("this reviewer flagged a single category-level
concern").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jig.reviewers.comment import ReviewerComment

# (reviewer, type, file, contract_uri_norm) — type and reviewer are
# the most stable discriminators; file scopes within the project; the
# normalized contract_uri carries AC identity so distinct ACs flagged
# at the same file (or same line) don't collapse, and re-raises across
# cycles stay attached to the original ID.
FindingSignature = tuple[str, str, str | None, str | None]


def _normalize_contract_uri(uri: str | None) -> str | None:
    """Canonicalize a ``contract_uri`` for signature comparison.

    Reviewers vary in how they spell the same AC across cycles —
    ``docs/brief.md#x`` vs ``brief.md#x``, ``Brief.md#X`` vs
    ``brief.md#x``. Without normalization those drift into distinct
    signatures and the re-raise of the same AC mints a fresh RC.

    Normalization: lowercase, then strip a single leading ``docs/`` or
    ``./`` segment. ``mydocs/`` is preserved (it's a different path).
    """
    if uri is None:
        return None
    out = uri.lower()
    if out.startswith("docs/"):
        out = out[len("docs/") :]
    elif out.startswith("./"):
        out = out[len("./") :]
    return out


def signature_of(comment: "ReviewerComment") -> FindingSignature:
    """Compute the finding signature for ``comment``.

    Public helper so callers (``fix_loop_bundle``, ``story``,
    ``ws_server``) don't reconstruct the tuple inline — keeping the
    signature schema in one place means widening it (adding
    ``contract_uri``, dropping ``line``) doesn't require coordinated
    edits at every callsite.
    """
    # ``type`` may be enum or coerced str depending on validation path.
    type_value = (
        comment.type.value if hasattr(comment.type, "value") else str(comment.type)
    )
    return (
        comment.reviewer,
        type_value,
        comment.file,
        _normalize_contract_uri(comment.contract_uri),
    )


# Internal alias kept for symmetry with prior versions; new code should
# call ``signature_of`` directly.
_signature = signature_of


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


__all__ = ["FindingSignature", "compute_finding_ids", "find_by_id", "signature_of"]
