---
title: Radon Code-Quality Signals — Design
type: design
status: active
owner: Brent Hoover
created: 2026-05-28
updated: 2026-05-28
problem: ./problem.md
---

# Radon Code-Quality Signals — Design

## Summary

Add a small, deterministic code-metrics computation — max cyclomatic complexity (radon), ruff finding
count, and lines-of-code delta — over the Python files a ticket changed, and surface it as a numeric block
in the LLM-driven reviewer prompts. The metric is computed by a single reusable, side-effect-free function
called from two sites: the worktree commit path (so the commit caller gets a logged signal) and the reviewer
dispatch path (so the spawned reviewer prompts get an objective metrics block). Nothing blocks a commit;
this is signal only.

## Approach

### New module: `jig/code_metrics.py`

A pure, dependency-light module exposing one entry point:

```
async def compute_change_metrics(
    worktree_path: Path,
    base_ref: str = "main",
) -> ChangeMetrics
```

`ChangeMetrics` (pydantic v2 model) carries:

- `max_cc: int` — largest cyclomatic complexity across all functions/methods in changed `.py` files
- `max_cc_location: str | None` — `"file.py:func_name"` of the function that hit `max_cc`
- `ruff_findings: int` — count of remaining ruff findings on the changed files
- `loc_delta: int` — net non-blank/non-comment line delta (added − removed) across changed `.py` files
- `flagged: bool` — `True` when `max_cc > CC_FLAG_THRESHOLD` (see Open question resolution)

Internals:

- Resolve the changed `.py` set via `git diff --name-only <base_ref>...HEAD` (plus working-tree changes for
  the commit-time call, which runs before `git add -A`).
- Reuse the existing radon logic. `max_cyclomatic(code: str) -> int` already lives in
  `jig/evals/prompt_style_eval/metrics.py:34` and is tested against real radon. Lift the radon-touching
  primitives (`max_cyclomatic`, the per-block CC walk) into `jig/code_metrics.py` and have the eval module
  import them from there, so there is exactly one radon call site in the codebase rather than two.
- `ruff_findings` reuses the existing ruff pass output where available; the commit path already runs ruff in
  `_auto_lint`, so the count is a by-product there.

All radon/ruff calls degrade gracefully: a `SyntaxError` from `cc_visit`, a missing `git`, or a ruff failure
yields a `ChangeMetrics` with the affected field zeroed/None and a logged warning — never an exception that
reaches the commit or dispatch caller. (Problem constraint: radon failures must not block commits.)

### Wiring point 1 — commit path (`jig/worktree.py`)

`_auto_lint()` already runs ruff and knows the worktree. After its ruff passes, `commit_worktree()` calls
`compute_change_metrics()` and logs the result. The commit return type changes from `str | None` (sha) to a
small result object carrying both the sha and the `ChangeMetrics`, so the orchestrator
(`_auto_commit_worktree`, `orchestrator.py:2768`) and `ticket_mcp.py:569` callers can forward the signal.
The metric is advisory — it is logged and made available, and the commit proceeds regardless of `max_cc`.

### Wiring point 2 — reviewer dispatch (`jig/reviewers/dispatch.py`)

The LLM-driven reviewers are spawned via `dispatch_with_llm_spawn` →
`orchestrator.spawn_review_agent_for_id` (`orchestrator.py:976`), which builds an `AgentSpawnContext` and
renders the prompt through `prompt_builder`. That prompt already has a structured-injection mechanism:
`verify_bundle` → `_verify_findings_section()` (`prompt_builder.py:678`). We add a sibling section,
`_code_metrics_section()`, fed by the `ChangeMetrics` computed at dispatch time, rendering e.g.:

```
## Objective code metrics (changed files)
- max cyclomatic complexity: 12  (foo.py:handle_request) — HIGH
- ruff findings: 0
- LoC delta: +340
```

Mechanical reviewers are deterministic and have no LLM prompt, so they receive nothing — the block is
injected only into the spawned (LLM) reviewer prompts.

## Interfaces

- `jig/code_metrics.py`: `compute_change_metrics(worktree_path, base_ref="main") -> ChangeMetrics` and the
  `ChangeMetrics` model. This is the one new public surface.
- `commit_worktree()` return type changes (sha-only → `{sha, metrics}` object). Internal callers updated;
  no external/CLI/wire surface depends on it.
- `prompt_builder` gains an optional `code_metrics: ChangeMetrics | None` parameter, rendered by a new
  section function. Absent metrics → no section (graceful).
- `jig/evals/prompt_style_eval/metrics.py` re-imports `max_cyclomatic` from `jig/code_metrics.py` instead of
  defining it — behavior-preserving refactor, existing `tests/evals/test_metrics.py` stays green.

## Data model

`ChangeMetrics` is transient (computed per commit / per dispatch), not persisted to a JSONL store in this
iteration. Eval-rubric reuse and analytics persistence are deferred (see Out of scope / Open questions).

## Alternatives considered

### Compute once at commit time and persist for the reviewer to read back

Compute `ChangeMetrics` only in `commit_worktree`, write it to a store (or checkpoint), and have dispatch
read it back. Rejected as the primary mechanism: it forces a new persistence shape and a commit→dispatch
data-threading contract for a metric that is cheap and deterministic to recompute. The commit and dispatch
paths are independent call sites with their own `worktree_path`/`base_ref` already in hand; recomputing is
simpler than persisting and avoids staleness if the worktree changes between commit and end-of-ticket review.

### Inject the metrics block into mechanical reviewers too

The mechanical reviewers (contract/cross-cutting/spec/intent compliance) are deterministic Python with no LLM
prompt — there is nothing to inject text into, and they already compute their own diffs. The numeric block
only changes behavior where an LLM reads it. Rejected as nonsensical for that reviewer class.

### Special-case which LLM reviewers receive the block

The issue floated handing the block only to "quality-judgment" reviewers. Rejected: the block is three lines
of cheap objective numbers with low noise, and the planner already controls which LLM reviewers run via
`reviewer_set`. Gating the block by reviewer id adds branching for no clear benefit. All spawned LLM
reviewers get it.

### Chosen: shared pure module, recomputed at each of the two call sites

One `compute_change_metrics()` used by both the commit path (logged signal) and the dispatch path (prompt
block). Honors the problem.md's "`_auto_lint()` or a companion function" requirement, keeps a single radon
call site, and matches the two-flavor reviewer architecture without new persistence.

## Risks

- **`commit_worktree` return-type change** touches three callers (`orchestrator.py` ×2, `ticket_mcp.py` ×1)
  plus tests. Mechanical but non-trivial blast radius; must update all call sites and their assertions.
- **Diff-set resolution inside bwrap/Docker.** `git diff` must work in the sandbox where agents commit. The
  commit path already runs git there, so the dependency is not new — but the `<base_ref>...HEAD` form needs
  the base ref to exist in the worktree. Fallback: if `base_ref` is unresolvable, fall back to working-tree
  diff against `HEAD` and log.
- **radon on generated code with syntax errors.** Covered by graceful degradation, but worth an explicit
  test: a changed file that doesn't parse must yield `max_cc=0` for that file, not crash the pipeline.
- **LoC-delta definition drift.** Reusing the eval harness's non-blank/non-comment `count_loc` keeps one
  definition; if we instead used raw `git diff --numstat` the two would disagree. Pin to `count_loc`.

## Out of scope

- Blocking or gating commits on complexity (flag only — problem non-goal).
- Persisting `ChangeMetrics` to a store for analytics or eval-rubric reuse (the issue's third integration
  point) — deferred to a follow-on once the signal proves useful.
- Non-Python files (problem non-goal).
- A configurable/per-project CC threshold policy — a single constant for now (see Open questions).
- Maintainability index, Halstead metrics, or any radon output beyond cyclomatic complexity.

## Open questions

Resolved from problem.md:

- **Which reviewers receive the block?** → All LLM-spawned reviewers, via the new prompt section. Mechanical
  reviewers get nothing (no prompt). Resolved above.
- **What CC value is worth surfacing vs. noise?** → Always *report* `max_cc`; additionally *flag* (`flagged=
  True`, "HIGH" annotation) when `max_cc > 10`. 10 is McCabe's classic ceiling and the radon B/C rank
  boundary. Encoded as a module constant `CC_FLAG_THRESHOLD = 10`.

Also resolved:

- **Split `loc_delta` by source vs test?** → No. Single net `loc_delta` for v1. The only consumer that
  benefits from a split is the test-adequacy reviewer; until we observe whether reviewers react to the block
  at all, the split is speculative. It is purely additive later (one model field, one renderer line, a
  `tests/`-prefix classification).
- **Per-commit cadence in scope?** → Yes, and it needs no special handling. Agents typically commit once,
  when done, so the commit-time metrics log effectively coincides with end-of-ticket. The commit-path log
  fires per commit; the prompt block rides the existing end-of-ticket LLM-spawn path. No per-commit
  mechanical-reviewer output gains a block.

## Change log

- 2026-05-28: Initial draft (Brent Hoover)
- 2026-05-28: Resolved all open questions (single net LoC delta; per-commit cadence accepted as-is);
  status draft → active / approved (Brent Hoover)
