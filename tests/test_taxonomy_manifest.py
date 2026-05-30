"""Validate the AI-audit taxonomy manifest:

- All 24 patterns present, unique ids
- Detection kinds are ``ruff`` (with a ref) or ``judgment`` (ref None)
- Every ``owning_reviewer`` is a known LLM reviewer id
- 10 deterministic (ruff) / 14 judgment, per the verified mapping
"""

from __future__ import annotations

from jig.code_quality.taxonomy import load_taxonomy
from jig.reviewers.dispatch import known_llm_reviewer_ids

VALID_KINDS = {"ruff", "judgment"}


def test_manifest_has_24_entries() -> None:
    entries = load_taxonomy()
    assert len(entries) == 24
    assert len({e.id for e in entries}) == 24


def test_detection_kinds_and_refs() -> None:
    for e in load_taxonomy():
        assert e.detection.kind in VALID_KINDS
        if e.detection.kind == "ruff":
            assert e.detection.ref, f"{e.id}: ruff entry needs a rule code"
        else:
            assert e.detection.ref is None


def test_owning_reviewers_are_known() -> None:
    known = known_llm_reviewer_ids()
    for e in load_taxonomy():
        assert e.owning_reviewer in known, (
            f"{e.id}: unknown reviewer {e.owning_reviewer}"
        )


def test_ten_ruff_entries() -> None:
    ruff = [e for e in load_taxonomy() if e.detection.kind == "ruff"]
    assert len(ruff) == 10
