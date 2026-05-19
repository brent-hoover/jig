"""Tests for ``jig.finding_ids`` — fix-loop-context step 1.

Pure functions over a chronologically-ordered list of comments:

  - ``compute_finding_ids(comments)`` → ``dict[signature, "RC-N"]``
  - ``find_by_id(comments, finding_id)`` → ``ReviewerComment | None``

Stable-ID guarantee: walking in insertion order assigns RC-N
sequentially per *first appearance* of a signature
(``(reviewer, type, file, line)``). Re-phrased findings collapse to the
same signature, so they get the same RC. Later additions only append
higher RCs — they never renumber prior IDs.
"""

from __future__ import annotations

from jig.finding_ids import compute_finding_ids, find_by_id
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity


def _c(
    reviewer: str,
    type_: ReviewerCommentType,
    *,
    file: str | None = None,
    line: int | None = None,
    prose: str = "p",
    cycle: int = 0,
) -> ReviewerComment:
    return ReviewerComment(
        type=type_,
        severity=Severity.IMPORTANT,
        reviewer=reviewer,
        prose=prose,
        file=file,
        line=line,
        cycle=cycle,
        ticket_id="t-1",
    )


PATTERN = ReviewerCommentType.PATTERN_DIVERGENCE
ERROR_HANDLING = ReviewerCommentType.ERROR_HANDLING


class TestSignatureGrouping:
    def test_distinct_signatures_get_sequential_ids(self) -> None:
        comments = [
            _c("reviewer-pattern-conformance", PATTERN, file="a.py", line=10),
            _c("reviewer-error-handling", ERROR_HANDLING, file="b.py", line=5),
            _c("reviewer-pattern-conformance", PATTERN, file="c.py", line=1),
        ]
        ids = compute_finding_ids(comments)
        # Each distinct signature is its own RC.
        assert set(ids.values()) == {"RC-1", "RC-2", "RC-3"}
        # IDs are assigned by first appearance in input order.
        sig1 = ("reviewer-pattern-conformance", PATTERN.value, "a.py", 10)
        sig2 = ("reviewer-error-handling", ERROR_HANDLING.value, "b.py", 5)
        sig3 = ("reviewer-pattern-conformance", PATTERN.value, "c.py", 1)
        assert ids[sig1] == "RC-1"
        assert ids[sig2] == "RC-2"
        assert ids[sig3] == "RC-3"

    def test_rephrased_finding_keeps_id(self) -> None:
        """Cycle-1 and cycle-3 with the same (reviewer, type, file, line)
        but different prose share an RC. This is the load-bearing case:
        the dev's ack of RC-3 must stay attached to the underlying
        logical finding even when the reviewer re-words it later."""
        comments = [
            _c("rev-a", PATTERN, file="x.py", line=10, prose="first phrasing", cycle=0),
            _c("rev-a", PATTERN, file="x.py", line=10, prose="reworded", cycle=2),
        ]
        ids = compute_finding_ids(comments)
        assert len(ids) == 1
        assert list(ids.values()) == ["RC-1"]


class TestInsertionOrderStability:
    def test_later_additions_do_not_renumber_earlier_ids(self) -> None:
        """Append a new finding after computing IDs for a prefix; the
        prefix's RC-N must not change. This is the property that lets us
        store an ack against RC-3 in cycle 1 and have it still resolve
        to the same finding in cycle 3."""
        prefix = [
            _c("rev-a", PATTERN, file="a.py", line=1),
            _c("rev-b", ERROR_HANDLING, file="b.py", line=2),
        ]
        prefix_ids = compute_finding_ids(prefix)
        full = prefix + [_c("rev-c", PATTERN, file="c.py", line=3)]
        full_ids = compute_finding_ids(full)
        for sig, rc in prefix_ids.items():
            assert full_ids[sig] == rc

    def test_categorical_signature_collapses_to_single_id(self) -> None:
        """A reviewer that doesn't supply file/line (diff-wide concern)
        produces ``(reviewer, type, None, None)`` — every such finding
        from that reviewer/type pair collapses to one RC."""
        comments = [
            _c("rev-a", PATTERN, prose="first diff-wide"),
            _c("rev-a", PATTERN, prose="second diff-wide"),
            _c("rev-a", PATTERN, prose="third diff-wide"),
        ]
        ids = compute_finding_ids(comments)
        assert len(ids) == 1
        assert list(ids.values()) == ["RC-1"]


class TestFindById:
    def test_returns_first_match(self) -> None:
        comments = [
            _c("rev-a", PATTERN, file="a.py", line=1, prose="original"),
            _c("rev-a", PATTERN, file="a.py", line=1, prose="reworded"),
            _c("rev-b", ERROR_HANDLING, file="b.py", line=5, prose="other"),
        ]
        # The RC-1 signature has two matching comments; find_by_id
        # returns one (the first by insertion).
        out = find_by_id(comments, "RC-1")
        assert out is not None
        assert out.reviewer == "rev-a"
        assert out.file == "a.py"
        assert out.line == 1

    def test_returns_none_for_unknown_id(self) -> None:
        comments = [_c("rev-a", PATTERN, file="a.py", line=1)]
        assert find_by_id(comments, "RC-99") is None

    def test_returns_none_for_empty_list(self) -> None:
        assert find_by_id([], "RC-1") is None

    def test_returns_match_for_higher_rc(self) -> None:
        comments = [
            _c("rev-a", PATTERN, file="a.py", line=1),
            _c("rev-b", ERROR_HANDLING, file="b.py", line=5),
        ]
        out = find_by_id(comments, "RC-2")
        assert out is not None
        assert out.reviewer == "rev-b"
