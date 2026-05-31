---
title: Sub-issue C — Reviewer-Facing Disposition for Taxonomy Findings — Implementation Plan
type: plan
status: active
owner: Brent Hoover
created: 2026-05-30
updated: 2026-05-31
design: ./design.md
---

# Reviewer-Facing Disposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Route the taxonomy findings (deterministic `TaxonomyHit`s + the judgment-only catalog) to each LLM
reviewer's prompt by `owning_reviewer`, so each reviewer sees only the items in its category.

**Architecture:** The existing `_code_metrics_section` (PR #107) renders one universal block for every
reviewer. C extends it: when the spawn is for a known LLM reviewer, append two per-reviewer pieces:

1. **Deterministic findings** — `ChangeMetrics.taxonomy_hits` filtered to entries whose `owning_reviewer`
   equals the current reviewer id; rendered as `[TAX-ID] file:line — cue`.
2. **Judgment checklist** — the static taxonomy entries with `detection.kind == "judgment"` and
   `owning_reviewer` equal to the current reviewer; rendered as `[TAX-ID] cue` so the LLM has a curated
   "look for these" list backed by `mechanism`.

Two small helpers in `jig/code_quality/taxonomy.py` do the filtering. `build_initial_prompt` already knows
the role (via `role_cfg.role`) — pass it through to `_code_metrics_section`. `dispatch_with_llm_spawn` logs
a warning when a `TaxonomyHit`'s `owning_reviewer` isn't in the spawned set (the design's "never silent
drop" contract; measurement-side capture lives in sub-issue D).

**Tech Stack:** Python 3.12, pydantic v2, pytest. No new deps.

## Scope (sub-issue C = #111)

In: per-reviewer filtering helpers, the prompt-section extension, role threading, the no-selected-reviewer
warning, and tests. Out: measurement/audit-store recording of hits (sub-issue D); broadening individual
judgment cues; new reviewer roles.

## File structure

- `jig/code_quality/taxonomy.py` — add `entries_for_reviewer()` + `hits_for_reviewer()` helpers.
- `jig/prompt_builder.py` — extend `_code_metrics_section(metrics, role)`; update call-site in
  `build_initial_prompt` to pass `role=role_cfg.role`.
- `jig/reviewers/dispatch.py` — after `compute_change_metrics` returns, log a warning for hits whose
  `owning_reviewer` isn't in the set of spawned reviewer ids.
- `tests/test_taxonomy_filters.py` (new) — helper tests.
- `tests/test_prompt_builder_metrics.py` (modify) — assert per-reviewer rendering.
- `tests/test_reviewers_federation_execution.py` (modify) — assert the dispatch warning fires.

## Task 1: filter helpers (TDD)

**Files:**
- Modify: `jig/code_quality/taxonomy.py`
- Test: `tests/test_taxonomy_filters.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_taxonomy_filters.py -q`
Expected: FAIL — `entries_for_reviewer` / `hits_for_reviewer` not defined.

- [ ] **Step 3: Implement the helpers**

Add to `jig/code_quality/taxonomy.py`:

```python
def entries_for_reviewer(reviewer_id: str) -> tuple[TaxonomyEntry, ...]:
    """Manifest entries whose ``owning_reviewer`` matches ``reviewer_id``.

    Returns an empty tuple for unknown ids — callers route based on the result
    and an unknown reviewer simply yields no block."""
    return tuple(e for e in load_taxonomy() if e.owning_reviewer == reviewer_id)


def hits_for_reviewer(
    hits: tuple[TaxonomyHit, ...] | list[TaxonomyHit],
    reviewer_id: str,
) -> tuple[TaxonomyHit, ...]:
    """Filter ``hits`` to those owned by ``reviewer_id``."""
    return tuple(h for h in hits if h.reviewer == reviewer_id)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_taxonomy_filters.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/code_quality/taxonomy.py tests/test_taxonomy_filters.py
git commit -m "feat(code-quality): per-reviewer taxonomy filter helpers (#111)"
```

## Task 2: extend `_code_metrics_section` to render per-reviewer (TDD)

**Files:**
- Modify: `jig/prompt_builder.py`
- Test: `tests/test_prompt_builder_metrics.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompt_builder_metrics.py`:

```python
def test_section_renders_per_reviewer_taxonomy_block() -> None:
    from jig.code_quality.taxonomy import TaxonomyHit

    metrics = ChangeMetrics(
        max_cc=3,
        max_cc_location="m.py:f",
        ruff_findings=0,
        loc_delta=5,
        taxonomy_hits=(
            TaxonomyHit(
                id="TAX-SEC-001",
                category="security",
                file="m.py",
                line=12,
                reviewer="reviewer-security",
            ),
            TaxonomyHit(
                id="TAX-LANG-001",
                category="language-pitfall",
                file="m.py",
                line=20,
                reviewer="reviewer-pattern-conformance",
            ),
        ),
    )
    out_sec = _code_metrics_section(metrics, role="reviewer-security")
    out_pc = _code_metrics_section(metrics, role="reviewer-pattern-conformance")

    # Universal piece still rendered for both.
    assert "## Objective Code Metrics" in out_sec
    assert "## Objective Code Metrics" in out_pc

    # Per-reviewer deterministic block: security sees its hit, not pc's.
    assert "TAX-SEC-001" in out_sec
    assert "TAX-LANG-001" not in out_sec
    assert "TAX-LANG-001" in out_pc
    assert "TAX-SEC-001" not in out_pc

    # Per-reviewer judgment checklist: pc owns plenty of judgment items;
    # security owns none (its three entries are all `detection: ruff`).
    assert "Judgment checklist" in out_pc
    assert "TAX-CF-001" in out_pc  # off-by-one belongs to pc
    assert "Judgment checklist" not in out_sec


def test_section_for_non_reviewer_role_omits_per_reviewer_block() -> None:
    metrics = ChangeMetrics(
        max_cc=0, max_cc_location=None, ruff_findings=0, loc_delta=0, taxonomy_hits=()
    )
    out = _code_metrics_section(metrics, role="dev")  # not in known_llm_reviewer_ids()

    # Universal piece renders; per-reviewer pieces don't.
    assert "## Objective Code Metrics" in out
    assert "Judgment checklist" not in out
    assert "Deterministic taxonomy findings" not in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_prompt_builder_metrics.py -q`
Expected: FAIL — `_code_metrics_section` does not accept `role`.

- [ ] **Step 3: Extend the section**

In `jig/prompt_builder.py`:

- Add an import: `from jig.code_quality.taxonomy import entries_for_reviewer, hits_for_reviewer`.
- Change the signature to `_code_metrics_section(metrics, role: str | None = None)`.
- After the existing universal block, compute:

  - `det_hits = hits_for_reviewer(metrics.taxonomy_hits, role) if role else ()`
  - `judgment = tuple(e for e in entries_for_reviewer(role) if e.detection.kind == "judgment") if role else ()`

  Then append each sub-block **only when its list is non-empty** (gating naturally falls out — non-reviewer
  roles get empty filters; reviewer roles with no matches skip the empty sub-block):

  - **"Deterministic taxonomy findings"** — one line per hit: `[TAX-ID] file:line — cue` (look up `cue`
    via the matching `TaxonomyEntry` in the loaded manifest).
  - **"Judgment checklist"** — one line per judgment entry: `[TAX-ID] cue`.

- In `build_initial_prompt`, change `_code_metrics_section(code_metrics)` to
  `_code_metrics_section(code_metrics, role=role_cfg.role)`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_prompt_builder_metrics.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/prompt_builder.py tests/test_prompt_builder_metrics.py
git commit -m "feat(code-quality): per-reviewer taxonomy block in reviewer prompts (#111)"
```

## Task 3: dispatch warning on no-selected-reviewer (TDD)

**Files:**
- Modify: `jig/reviewers/dispatch.py`
- Test: `tests/test_reviewers_federation_execution.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reviewers_federation_execution.py`:

```python
@pytest.mark.asyncio
async def test_unmatched_taxonomy_hits_warn_no_silent_drop(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """Per the design's 'no silent drop' contract: if a hit's owning_reviewer
    isn't in the spawned set, a warning fires (the hit still travels into the
    reviewer prompts that ARE spawned — measurement-side capture is D)."""
    import logging

    from jig.code_metrics import ChangeMetrics
    from jig.code_quality.taxonomy import TaxonomyHit
    from jig.reviewers import dispatch as dispatch_mod

    # Stub compute_change_metrics so we control the hits.
    async def fake_compute(*_a, **_k):
        return ChangeMetrics(
            max_cc=0,
            max_cc_location=None,
            ruff_findings=0,
            loc_delta=0,
            taxonomy_hits=(
                TaxonomyHit(
                    id="TAX-TEST-001",
                    category="testing",
                    file="x.py",
                    line=1,
                    reviewer="reviewer-test-adequacy",
                ),
            ),
        )

    monkeypatch.setattr(dispatch_mod, "compute_change_metrics", fake_compute)

    _write_arch(tmp_path)
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
    _init_worktree(worktree)

    orch = _FakeOrchestrator()
    with caplog.at_level(logging.WARNING, logger="jig.reviewers.dispatch"):
        await dispatch_with_llm_spawn(
            _ticket(labels=["touches-auth"]),  # spawns security, NOT test-adequacy
            tmp_path,
            orch,
            worktree_path=worktree,
        )

    assert any(
        "TAX-TEST-001" in rec.message and "reviewer-test-adequacy" in rec.message
        for rec in caplog.records
    ), "expected a warning naming the unrouted hit"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_reviewers_federation_execution.py::test_unmatched_taxonomy_hits_warn_no_silent_drop -q`
Expected: FAIL — no warning emitted.

- [ ] **Step 3: Emit the warning in dispatch**

In `jig/reviewers/dispatch.py`, after `compute_change_metrics` returns (the call added in PR #107),
collect the set of spawned reviewer ids (`{p.reviewer_id for p in pendings}`) and for every
`code_metrics.taxonomy_hits` whose `reviewer` isn't in that set, log at warning level — listing the
taxonomy id, owning reviewer, file:line, and the spawned set (so the message is self-contained for the
audit log).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_reviewers_federation_execution.py -q`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add jig/reviewers/dispatch.py tests/test_reviewers_federation_execution.py
git commit -m "feat(code-quality): warn on unrouted taxonomy hits in dispatch (#111)"
```

## Task 4: full gate

- [ ] **Step 1: Lint + format** — `uv run ruff check jig/ tests/` + `ruff format --check` on changed files.
- [ ] **Step 2: Full suite** — `uv run pytest tests/ -q`; expect baseline +5 (3 filter helpers, 2 section,
  1 dispatch).
- [ ] **Step 3: Manual sanity** — `uv run python -c "from jig.code_quality.taxonomy import
  entries_for_reviewer; print([e.id for e in entries_for_reviewer('reviewer-pattern-conformance')])"`
  prints the entries for that reviewer.

## Rollback

Additive. Revert the branch. No store changes, no migrations.

## Out of scope for this plan

- Recording taxonomy hits in `AuditStore` for measurement/attribution (sub-issue D).
- Broadening individual judgment cues or adding new patterns to the manifest.
- Creating new reviewer roles.

## Change log

- 2026-05-30: Initial draft (Brent Hoover)
- 2026-05-31: Approved; status draft → active (Brent Hoover)
