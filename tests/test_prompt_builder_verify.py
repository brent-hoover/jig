"""Tests for the Previous Cycle Findings prompt section —
fix-loop-context step 4."""

from __future__ import annotations

import pytest

from jig.prompt_builder import _verify_findings_section


@pytest.fixture
def bundle_one_addressed() -> dict:
    return {
        "findings": [
            {
                "finding_id": "RC-3",
                "file": "src/main.py",
                "line": 23,
                "severity": "important",
                "reviewer": "reviewer-pattern-conformance",
                "original_prose": "_HN_BASE duplicates BASE",
                "dev_claim": {
                    "cycle": 1,
                    "author": "dev",
                    "prose": "removed _HN_BASE; main.py imports BASE from api",
                },
                "status": "addressed",
            }
        ],
    }


@pytest.fixture
def bundle_with_resolved_and_open() -> dict:
    return {
        "findings": [
            {
                "finding_id": "RC-1",
                "file": "src/api.py",
                "line": 5,
                "severity": "important",
                "reviewer": "reviewer-error-handling",
                "original_prose": "missing None handling",
                "dev_claim": {
                    "cycle": 1,
                    "author": "dev",
                    "prose": "added None guard",
                },
                "status": "resolved",
            },
            {
                "finding_id": "RC-3",
                "file": "src/main.py",
                "line": 23,
                "severity": "important",
                "reviewer": "reviewer-pattern-conformance",
                "original_prose": "_HN_BASE duplicates BASE",
                "dev_claim": None,
                "status": "open",
            },
        ],
    }


def test_empty_bundle_renders_nothing() -> None:
    assert _verify_findings_section({"findings": []}) == ""
    assert _verify_findings_section(None) == ""


def test_renders_two_task_framing(bundle_one_addressed: dict) -> None:
    out = _verify_findings_section(bundle_one_addressed)
    assert "## Previous Cycle Findings" in out
    assert "Verify" in out
    assert "Find new issues" in out or "find new issues" in out
    assert "mark_finding_resolved" in out


def test_renders_finding_metadata(bundle_one_addressed: dict) -> None:
    out = _verify_findings_section(bundle_one_addressed)
    assert "RC-3" in out
    assert "src/main.py" in out
    assert "23" in out
    assert "_HN_BASE duplicates BASE" in out
    assert "removed _HN_BASE" in out  # dev's claim shown


def test_renders_no_claim_marker_when_no_dev_ack(
    bundle_with_resolved_and_open: dict,
) -> None:
    out = _verify_findings_section(bundle_with_resolved_and_open)
    # RC-3 has no dev_claim — must be clearly marked as not addressed
    # so the reviewer doesn't mistake silence for a claimed fix.
    assert "no claim" in out.lower() or "not addressed" in out.lower()


def test_resolved_finding_marked(bundle_with_resolved_and_open: dict) -> None:
    out = _verify_findings_section(bundle_with_resolved_and_open)
    # The reviewer should see "already resolved" markers so they don't
    # waste effort re-verifying things that previous reviewers
    # confirmed.
    assert "resolved" in out.lower()
