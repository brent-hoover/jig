---
title: Radon Code-Quality Signals — Implementation Plan
type: plan
status: active
owner: Brent Hoover
created: 2026-05-28
updated: 2026-05-28
design: ./design.md
---

# Radon Code-Quality Signals — Implementation Plan

## Overview

Build the deterministic code-metrics signal in three vertical slices, ordered to push the blast-radius down
the stack: first the pure, self-contained metrics module (no callers affected), then the commit-path log
(no signature change), then the reviewer-prompt injection. Each slice is independently testable and
mergeable. TDD throughout — tests run against real radon/ruff/git, not mocks (both tools are project deps and
the eval suite already exercises real radon).

## Preconditions

- [x] Design approved (`design.md` status: active).
- [x] `radon>=6.0.1` and `ruff` already in `pyproject.toml` — no new deps.
- [ ] Work happens on a worktree under `.worktrees/`, not on `develop`.

## Steps

### 1. Pure metrics module `jig/code_metrics.py`

**What:**
- New `jig/code_metrics.py` exposing:
  - `ChangeMetrics` (pydantic v2): `max_cc: int`, `max_cc_location: str | None`, `ruff_findings: int`,
    `loc_delta: int`, `flagged: bool`.
  - `CC_FLAG_THRESHOLD = 10` module constant; `flagged = max_cc > CC_FLAG_THRESHOLD`.
  - `max_cyclomatic(code: str) -> int` and a `cc_with_location(code) -> tuple[int, str | None]` helper,
    lifted from `jig/evals/prompt_style_eval/metrics.py`.
  - `count_loc(code: str) -> int`, also lifted (single definition of "non-blank/non-comment line").
  - `async def compute_change_metrics(worktree_path, base_ref="main") -> ChangeMetrics`: resolve changed
    `.py` files via `git diff`, read each, compute max CC + location, ruff finding count, net LoC delta.
- Repoint `jig/evals/prompt_style_eval/metrics.py` to import `max_cyclomatic`/`count_loc` from
  `jig.code_metrics` (behavior-preserving — no duplicate radon call site).

**Why:** Self-contained core with zero caller impact; everything else depends on it.

**Verify:**
- New `tests/test_code_metrics.py`: real-radon cases — clean snippet (`max_cc` small, `flagged=False`),
  high-CC function (`flagged=True`, location correct), `SyntaxError` file → `max_cc=0` no raise, no-`.py`
  changes → zeroed metrics, `git` missing/`base_ref` unresolvable → graceful fallback + logged warning.
- `uv run pytest tests/evals/test_metrics.py` stays green (import refactor didn't change behavior).

### 2. Commit-path signal (`jig/worktree.py`)

**What:**
- Introduce a small result type `CommitResult` (pydantic v2 or frozen dataclass): `sha: str | None`,
  `metrics: ChangeMetrics | None`.
- `commit_worktree()` return type changes `str | None` → `CommitResult`. After `_auto_lint()` succeeds,
  call `compute_change_metrics(worktree_path)` (wrapped so a metrics failure logs and yields `metrics=None`,
  never blocks the commit) and also `_logger.info(...)` the result.
- Update the three callers and their assertions:
  - `orchestrator.py:2782` (`_auto_commit_worktree`) — read `.sha` (behavior unchanged).
  - `ticket_mcp.py:569` — read `.sha`; the "lint passed" path (`:595`) is unchanged.
  - any test asserting on the old sha return.
- Dispatch still recomputes its own metrics (design decision: recompute at each site, do not thread/persist).
  The `CommitResult.metrics` field exists for the commit-time log now and a future analytics/persistence
  consumer; it is deliberately not forwarded to dispatch.

**Why:** Gives an operator-visible per-commit complexity signal and a typed home for the metric on the commit
result, without coupling the commit and dispatch paths.

**Verify:**
- Worktree test: commit a worktree containing a high-CC file → `CommitResult.sha` is a real sha,
  `.metrics.max_cc` matches, `.metrics.flagged is True`, metrics log line emitted (caplog).
- A syntactically-broken changed file → commit still succeeds, `.metrics` is `None` or zeroed, no raise.
- All three updated callers' tests green.

### 3. Reviewer-prompt metrics block

**What:**
- Add `_code_metrics_section(metrics: ChangeMetrics | None) -> str` to `jig/prompt_builder.py`, rendered into
  the reviewer prompt next to the existing `_verify_findings_section()` call (`prompt_builder.py:678`).
  Absent metrics → empty string (graceful).
- Thread an optional `code_metrics: ChangeMetrics | None` param through the reviewer prompt builder.
- In the LLM-spawn path: compute metrics once in `dispatch_with_llm_spawn`
  (`jig/reviewers/dispatch.py`, has `worktree_path`/`base_ref` in hand) and pass into
  `orchestrator.spawn_review_agent_for_id` → `AgentSpawnContext` → prompt builder. Mechanical reviewers
  receive nothing.

**Why:** Delivers the actual product goal — objective numbers in front of the LLM reviewers.

**Verify:**
- `tests/` prompt-builder test: given a `ChangeMetrics`, rendered prompt contains the metrics block with the
  CC line + "HIGH" annotation when flagged; given `None`, no block appears.
- Dispatch test (mock orchestrator, as existing federation tests do): `spawn_review_agent_for_id` receives a
  populated `code_metrics` for an LLM reviewer; mechanical reviewers' path is unaffected.

### 4. Full verification

**What:** Run the complete gate locally.

**Verify:**
- `uv run ruff check jig/ tests/`
- `uv run ruff format --check jig/ tests/`
- `uv run pytest tests/ -q` — full suite green (baseline was 4231 passing).

## Rollback

Pure-additive feature with no migrations and no persisted state. Rollback = revert the branch. Step 1's eval
import refactor is the only change to existing behavior; if it regressed, `tests/evals/test_metrics.py` would
fail at step 1 and the slice would not merge.

## Out of scope for this plan

- Persisting `ChangeMetrics` to any store / analytics surface (deferred follow-on).
- Source/test LoC split (Option B) — single net `loc_delta` only.
- Blocking or gating commits on complexity.
- Non-Python files; radon metrics beyond cyclomatic complexity.

## Change log

- 2026-05-28: Initial draft (Brent Hoover)
- 2026-05-28: Resolved Step 2 to use the `commit_worktree` return-type change (`CommitResult`); status
  draft → active / approved (Brent Hoover)
