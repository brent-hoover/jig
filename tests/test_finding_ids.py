"""Tests for ``jig.finding_ids`` — fix-loop-context step 1.

Pure functions over a chronologically-ordered list of comments:

  - ``compute_finding_ids(comments)`` → ``dict[signature, "RC-N"]``
  - ``find_by_id(comments, finding_id)`` → ``ReviewerComment | None``

Stable-ID guarantee: walking in insertion order assigns RC-N
sequentially per *first appearance* of a signature
(``(reviewer, type, file, contract_uri_norm)``). Re-phrased findings
collapse to the same signature, so they get the same RC. Later
additions only append higher RCs — they never renumber prior IDs.

``contract_uri`` is normalized before signature comparison so that
re-raises across cycles which drop/add the ``docs/`` prefix (or vary in
case) still resolve to the same finding. ``line`` is intentionally
*not* part of the signature — the reviewer line is an aid for the dev
to locate code, not a stable identifier, and including it broke
cross-cycle continuity once the test file grew.
"""

from __future__ import annotations

from jig.finding_ids import _normalize_contract_uri, compute_finding_ids, find_by_id
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity


def _c(
    reviewer: str,
    type_: ReviewerCommentType,
    *,
    file: str | None = None,
    line: int | None = None,
    contract_uri: str | None = None,
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
        contract_uri=contract_uri,
        cycle=cycle,
        ticket_id="t-1",
    )


PATTERN = ReviewerCommentType.PATTERN_DIVERGENCE
ERROR_HANDLING = ReviewerCommentType.ERROR_HANDLING
TEST_ADEQUACY = ReviewerCommentType.TEST_ADEQUACY


class TestSignatureGrouping:
    def test_distinct_signatures_get_sequential_ids(self) -> None:
        comments = [
            _c(
                "reviewer-pattern-conformance",
                PATTERN,
                file="a.py",
                contract_uri="docs/spec.md#x",
            ),
            _c(
                "reviewer-error-handling",
                ERROR_HANDLING,
                file="b.py",
                contract_uri="docs/spec.md#y",
            ),
            _c(
                "reviewer-pattern-conformance",
                PATTERN,
                file="c.py",
                contract_uri="docs/spec.md#z",
            ),
        ]
        ids = compute_finding_ids(comments)
        assert set(ids.values()) == {"RC-1", "RC-2", "RC-3"}
        sig1 = ("reviewer-pattern-conformance", PATTERN.value, "a.py", "spec.md#x")
        sig2 = ("reviewer-error-handling", ERROR_HANDLING.value, "b.py", "spec.md#y")
        sig3 = ("reviewer-pattern-conformance", PATTERN.value, "c.py", "spec.md#z")
        assert ids[sig1] == "RC-1"
        assert ids[sig2] == "RC-2"
        assert ids[sig3] == "RC-3"

    def test_same_line_different_contract_uris_get_distinct_ids(self) -> None:
        """Regression: three reviewer comments at the same insertion-point
        line but flagging three different AC items must be three distinct
        findings. Previously they all collapsed to one RC because the
        signature keyed on (file, line) — the agent would ack one and the
        other two would silently re-surface in later cycles."""
        comments = [
            _c(
                "rta",
                TEST_ADEQUACY,
                file="tests/test_top.py",
                line=649,
                contract_uri="docs/brief.md#filter-by-score",
            ),
            _c(
                "rta",
                TEST_ADEQUACY,
                file="tests/test_top.py",
                line=649,
                contract_uri="docs/brief.md#filter-by-type",
            ),
            _c(
                "rta",
                TEST_ADEQUACY,
                file="tests/test_top.py",
                line=649,
                contract_uri="docs/brief.md#format-output",
            ),
        ]
        ids = compute_finding_ids(comments)
        assert set(ids.values()) == {"RC-1", "RC-2", "RC-3"}

    def test_rephrased_finding_at_new_line_keeps_id(self) -> None:
        """Re-raising the same AC across cycles must keep its RC even when
        the file grew and the reviewer's insertion-point line moved. This
        is the load-bearing property for fix-loop continuity: the dev's
        ack of RC-3 in cycle 1 must still match the same RC-3 in cycle 3
        when the reviewer flags the same concern at line 940 instead of
        line 649."""
        comments = [
            _c(
                "rta",
                TEST_ADEQUACY,
                file="tests/test_top.py",
                line=649,
                contract_uri="docs/brief.md#filter-by-type",
                cycle=0,
            ),
            _c(
                "rta",
                TEST_ADEQUACY,
                file="tests/test_top.py",
                line=940,
                contract_uri="docs/brief.md#filter-by-type",
                cycle=2,
            ),
        ]
        ids = compute_finding_ids(comments)
        assert len(ids) == 1
        assert list(ids.values()) == ["RC-1"]

    def test_contract_uri_prefix_variants_collapse(self) -> None:
        """A reviewer that writes ``brief.md#x`` in one cycle and
        ``docs/brief.md#x`` in the next is flagging the same AC; both
        must resolve to the same RC. Normalization strips a leading
        ``docs/`` and lowercases — anything else is treated as a distinct
        URI on purpose so unrelated paths don't accidentally collapse."""
        comments = [
            _c(
                "rta",
                TEST_ADEQUACY,
                file="t.py",
                contract_uri="docs/brief.md#filter-by-type",
                cycle=0,
            ),
            _c(
                "rta",
                TEST_ADEQUACY,
                file="t.py",
                contract_uri="brief.md#filter-by-type",
                cycle=1,
            ),
            _c(
                "rta",
                TEST_ADEQUACY,
                file="t.py",
                contract_uri="Brief.md#Filter-By-Type",
                cycle=2,
            ),
        ]
        ids = compute_finding_ids(comments)
        assert len(ids) == 1

    def test_rephrased_prose_same_signature_keeps_id(self) -> None:
        """Cycle-0 and cycle-2 with the same signature but different prose
        share an RC — the reviewer's wording can drift across cycles but
        the underlying finding identity must not."""
        comments = [
            _c(
                "rev-a",
                PATTERN,
                file="x.py",
                contract_uri="docs/spec.md#a",
                prose="first phrasing",
                cycle=0,
            ),
            _c(
                "rev-a",
                PATTERN,
                file="x.py",
                contract_uri="docs/spec.md#a",
                prose="reworded",
                cycle=2,
            ),
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
            _c("rev-a", PATTERN, file="a.py", contract_uri="docs/s.md#a"),
            _c("rev-b", ERROR_HANDLING, file="b.py", contract_uri="docs/s.md#b"),
        ]
        prefix_ids = compute_finding_ids(prefix)
        full = prefix + [
            _c("rev-c", PATTERN, file="c.py", contract_uri="docs/s.md#c"),
        ]
        full_ids = compute_finding_ids(full)
        for sig, rc in prefix_ids.items():
            assert full_ids[sig] == rc

    def test_categorical_signature_collapses_to_single_id(self) -> None:
        """A reviewer that doesn't supply file or contract_uri (diff-wide
        concern) produces ``(reviewer, type, None, None)`` — every such
        finding from that reviewer/type pair collapses to one RC."""
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
            _c(
                "rev-a",
                PATTERN,
                file="a.py",
                contract_uri="docs/s.md#a",
                prose="original",
            ),
            _c(
                "rev-a",
                PATTERN,
                file="a.py",
                contract_uri="docs/s.md#a",
                prose="reworded",
            ),
            _c(
                "rev-b",
                ERROR_HANDLING,
                file="b.py",
                contract_uri="docs/s.md#b",
                prose="other",
            ),
        ]
        out = find_by_id(comments, "RC-1")
        assert out is not None
        assert out.reviewer == "rev-a"
        assert out.file == "a.py"
        assert out.prose == "original"

    def test_returns_none_for_unknown_id(self) -> None:
        comments = [
            _c("rev-a", PATTERN, file="a.py", contract_uri="docs/s.md#a"),
        ]
        assert find_by_id(comments, "RC-99") is None

    def test_returns_none_for_empty_list(self) -> None:
        assert find_by_id([], "RC-1") is None

    def test_returns_match_for_higher_rc(self) -> None:
        comments = [
            _c("rev-a", PATTERN, file="a.py", contract_uri="docs/s.md#a"),
            _c("rev-b", ERROR_HANDLING, file="b.py", contract_uri="docs/s.md#b"),
        ]
        out = find_by_id(comments, "RC-2")
        assert out is not None
        assert out.reviewer == "rev-b"


class TestNormalizeContractUri:
    def test_strips_leading_docs_prefix(self) -> None:
        assert _normalize_contract_uri("docs/brief.md#x") == "brief.md#x"

    def test_strips_leading_dotslash(self) -> None:
        assert _normalize_contract_uri("./brief.md#x") == "brief.md#x"

    def test_lowercases(self) -> None:
        assert _normalize_contract_uri("Brief.md#Filter-By-Type") == (
            "brief.md#filter-by-type"
        )

    def test_idempotent_on_already_normalized(self) -> None:
        assert _normalize_contract_uri("brief.md#x") == "brief.md#x"

    def test_none_passthrough(self) -> None:
        assert _normalize_contract_uri(None) is None

    def test_does_not_strip_docs_inside_path(self) -> None:
        # ``mydocs/brief.md`` is a different path; only a leading
        # ``docs/`` segment is canonical.
        assert _normalize_contract_uri("mydocs/brief.md#x") == "mydocs/brief.md#x"
