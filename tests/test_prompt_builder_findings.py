"""Tests for the Blocking Findings prompt section — fix-loop-context step 3."""

from __future__ import annotations

import pytest

from jig.prompt_builder import _blocking_findings_section


@pytest.fixture
def bundle_one_finding() -> dict:
    return {
        "findings": [
            {
                "finding_id": "RC-3",
                "file": "src/main.py",
                "line": 23,
                "severity": "important",
                "reviewer": "reviewer-pattern-conformance",
                "prose": "_HN_BASE duplicates BASE in api.py",
                "ack_history": [],
            }
        ],
        "overflow_count": 0,
    }


@pytest.fixture
def bundle_with_acks() -> dict:
    return {
        "findings": [
            {
                "finding_id": "RC-3",
                "file": "src/main.py",
                "line": 23,
                "severity": "important",
                "reviewer": "reviewer-pattern-conformance",
                "prose": "_HN_BASE duplicates BASE",
                "ack_history": [
                    {
                        "kind": "addressed",
                        "author": "dev",
                        "cycle": 1,
                        "prose": "removed the duplicate",
                    },
                    {
                        "kind": "reraised",
                        "author": "orchestrator",
                        "cycle": 2,
                        "prose": "_HN_BASE still present after dev's commit",
                    },
                ],
            }
        ],
        "overflow_count": 0,
    }


def test_renders_header_and_instruction() -> None:
    bundle = {"findings": [], "overflow_count": 0}
    out = _blocking_findings_section(bundle)
    # Empty bundle should render nothing — the section only matters when
    # there are findings.
    assert out == ""


def test_renders_one_finding(bundle_one_finding: dict) -> None:
    out = _blocking_findings_section(bundle_one_finding)
    assert "## Blocking Findings" in out
    assert "RC-3" in out
    assert "src/main.py" in out
    assert "23" in out
    assert "important" in out
    assert "reviewer-pattern-conformance" in out
    assert "_HN_BASE duplicates BASE in api.py" in out
    assert "mark_finding_addressed" in out


def test_renders_ack_history(bundle_with_acks: dict) -> None:
    out = _blocking_findings_section(bundle_with_acks)
    assert "RC-3" in out
    # Each ack rendered with kind, cycle, author, prose
    assert "addressed" in out
    assert "removed the duplicate" in out
    assert "reraised" in out
    assert "_HN_BASE still present after dev's commit" in out


def test_overflow_count_rendered() -> None:
    bundle = {
        "findings": [
            {
                "finding_id": f"RC-{i}",
                "file": "a.py",
                "line": i,
                "severity": "important",
                "reviewer": "rev",
                "prose": f"finding {i}",
                "ack_history": [],
            }
            for i in range(1, 31)
        ],
        "overflow_count": 5,
    }
    out = _blocking_findings_section(bundle)
    assert "5 more" in out
    assert ".jig/store/review_comments.jsonl" in out


def test_no_overflow_when_count_is_zero(bundle_one_finding: dict) -> None:
    out = _blocking_findings_section(bundle_one_finding)
    assert "more —" not in out  # no overflow line
