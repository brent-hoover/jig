"""Per-reviewer filter helpers — slice the taxonomy by ``owning_reviewer`` so
each LLM reviewer's prompt only shows the items in its category."""

from __future__ import annotations

from jig.code_quality.taxonomy import (
    TaxonomyHit,
    entries_for_reviewer,
    hits_for_reviewer,
)


def test_entries_for_reviewer_filters_by_owning_reviewer() -> None:
    sec = entries_for_reviewer("reviewer-security")
    assert sec, "reviewer-security should own at least one entry"
    assert all(e.owning_reviewer == "reviewer-security" for e in sec)
    assert any(e.id == "TAX-SEC-001" for e in sec)


def test_entries_for_reviewer_unknown_returns_empty() -> None:
    assert entries_for_reviewer("nope") == ()


def test_hits_for_reviewer_filters_by_reviewer() -> None:
    hits = (
        TaxonomyHit(
            id="TAX-LANG-001",
            category="language-pitfall",
            file="a.py",
            line=1,
            reviewer="reviewer-pattern-conformance",
        ),
        TaxonomyHit(
            id="TAX-SEC-001",
            category="security",
            file="b.py",
            line=2,
            reviewer="reviewer-security",
        ),
    )
    out = hits_for_reviewer(hits, "reviewer-security")
    assert len(out) == 1
    assert out[0].id == "TAX-SEC-001"
