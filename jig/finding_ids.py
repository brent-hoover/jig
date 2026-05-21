"""Stable per-ticket finding IDs.

Pure functions over a chronologically-ordered list of
``ReviewerComment`` rows. The store layer is responsible for handing
us a deterministic order
(:meth:`jig.store.review_comments.ReviewCommentsStore.for_ticket_chronological`);
this module turns that order into stable ``RC-N`` identifiers that
survive re-phrasing and additional cycles.

**Signature.** Two findings are "the same finding" when they share
``(reviewer, type, file, discriminator)``. The discriminator is the
normalized ``contract_uri`` when the reviewer supplies one, falling
back to ``line`` otherwise. A reviewer that re-words the same
underlying concern across cycles produces the same signature and gets
the same ``RC-N``.

**Why the fallback.** ``contract_uri`` is the cleanest cross-cycle
anchor — it carries AC identity and stays stable when the file grows
and the reviewer's insertion-point line moves. But not every reviewer
sets ``contract_uri``: judgment reviewers (pattern-conformance,
error-handling, etc.) flag diff-anchored issues where no AC framing
applies. For those, ``line`` is the only practical discriminator —
without it two distinct findings from the same reviewer/type in the
same file would collapse into one RC and the dev would ack only one.

**Why not include ``line`` when ``contract_uri`` is present.** Using
``line`` alongside the URI would break cross-cycle continuity once
the test file grew: the reviewer would point at a new "where to
insert the test" line, mint a fresh signature, and the dev's prior
ack would silently detach. The URI alone is enough to identify
contract-anchored findings across cycles, and within-cycle
distinction comes from the URI itself (different AC = different URI).

**ID assignment.** Walk the comments in insertion order. On first
appearance of a signature, assign the next ``RC-N``. On re-appearance,
reuse the existing ``RC-N``. Insertion order means new findings only
ever append higher IDs; prior acks against ``RC-3`` always point at the
same logical finding.

The signature collapses to ``(reviewer, type, None, None)`` when a
reviewer omits ``file``, ``contract_uri``, and ``line`` — every
diff-wide finding from a (reviewer, type) pair collapses to one ID,
matching the natural interpretation ("this reviewer flagged a single
category-level concern").
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jig.reviewers.comment import ReviewerComment

# (reviewer, type, file, discriminator) — type and reviewer are the
# most stable discriminators; file scopes within the project. The
# fourth element is the *primary* AC identity (normalized
# ``contract_uri``) when the reviewer provides one, falling back to
# ``line`` when it doesn't.
#
# ``contract_uri`` is preferred because it survives cycles where the
# file has grown and the reviewer's insertion-point line has moved.
# ``line`` is the fallback so reviewers that flag diff-anchored issues
# without an AC framing (pattern-conformance, error-handling) don't
# collapse multiple distinct findings in the same file into one RC.
FindingSignature = tuple[str, str, str | None, str | int | None]


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
    signature schema in one place means widening it doesn't require
    coordinated edits at every callsite.

    Discriminator selection:

    - Reviewer supplied ``contract_uri`` → use the normalized URI. AC
      identity is the cleanest cross-cycle anchor.
    - Reviewer omitted ``contract_uri`` → fall back to ``line`` so
      diff-anchored findings (pattern-conformance, error-handling)
      that flag distinct issues in the same file don't collapse.
    - Neither set → ``None`` (the "diff-wide categorical finding"
      collapse is preserved).
    """
    # ``type`` may be enum or coerced str depending on validation path.
    type_value = (
        comment.type.value if hasattr(comment.type, "value") else str(comment.type)
    )
    uri_norm = _normalize_contract_uri(comment.contract_uri)
    discriminator: str | int | None
    if uri_norm is not None:
        discriminator = uri_norm
    else:
        discriminator = comment.line
    return (
        comment.reviewer,
        type_value,
        comment.file,
        discriminator,
    )


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
        sig = signature_of(comment)
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
        if ids.get(signature_of(comment)) == finding_id:
            return comment
    return None


__all__ = ["FindingSignature", "compute_finding_ids", "find_by_id", "signature_of"]
