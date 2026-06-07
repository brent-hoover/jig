---
title: Restore Brief for Project Analyzer — Implementation Plan
type: plan
status: draft
owner: Brent Hoover
created: 2026-06-07
updated: 2026-06-07
problem: ./problem.md
---

# Restore Brief for Project Analyzer — Implementation Plan

## Overview

One edit to `analyze()` in `jig/evals/watcher/analyzer.py`: when `brief_path` is `None`, try
`project_path / "docs" / "brief.md"` before proceeding. This fixes both the orchestrator path
(which never passes `brief_path`) and any future caller without touching their call sites.
Tests come second and cover both the found and not-found branches.

## Preconditions

- [ ] Problem statement approved.
- [ ] No existing `tests/test_analyzer*.py` files to conflict with (confirmed: none exist).

## Steps

### 1. Add canonical brief fallback to `analyze()`

**What:** In `jig/evals/watcher/analyzer.py`, immediately after the `analyze()` signature (line
296), add:

```python
if brief_path is None:
    candidate = project_path / "docs" / "brief.md"
    if candidate.is_file():
        brief_path = candidate
```

**Why:** Fixes the root cause at the single point where `brief_path` is consumed, without
requiring any caller to be updated. The candidate uses `project_path` as passed (unresolved),
matching how the CLI and `llm.py` handle it — resolving `project_path` first is unnecessary here.

**Verify:** `uv run ruff check jig/evals/watcher/analyzer.py && uv run ruff format --check
jig/evals/watcher/analyzer.py` passes clean.

### 2. Add regression tests

**What:** Create `tests/test_analyzer_brief_fallback.py` with two tests:

- `test_brief_auto_detected`: fixture a `tmp_path` with `docs/brief.md` present and minimal
  `.jig/store/` (empty JSONL files). Monkeypatch `jig.evals.watcher.llm.run_llm_analysis` (the
  import inside `analyze()` resolves there, not at the call site). Call `analyze(..., use_llm=True)`.
  Assert the captured `brief_path` kwarg equals `project_path / "docs" / "brief.md"`.
- `test_brief_absent_preserves_behavior`: same fixture without `docs/brief.md`; call
  `analyze(..., use_llm=True)` with the same monkeypatch; assert the captured `brief_path` kwarg
  is `None`.

**Why:** Prevents regression if the fallback is accidentally removed or the path constant changes.

**Verify:** `uv run pytest tests/test_analyzer_brief_fallback.py -v` — both tests pass.

### 3. Close issue and open PR

**What:** Commit, push branch, open PR referencing issue #82. Run full test suite before pushing.

**Why:** Ships the fix.

**Verify:** `uv run pytest tests/ -x` passes; CI green; PR description includes `Closes #82`.

## Rollback

Revert the single added block in `analyze()`. No data migrations, no schema changes.

## Out of scope for this plan

- Changing the CLI's `evals/projects/<name>/brief.md` auto-detect path.
- Any other changes to analyzer inputs, outputs, or LLM prompt construction.

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
