---
title: Code-Quality System — Problem Statement
type: problem
status: active
owner: Brent Hoover
created: 2026-05-28
updated: 2026-05-28
---

# Code-Quality System — Problem Statement

Tracked on GitHub as #108 (umbrella) with sub-issues #109–#112. Supersedes #17 ("Canonicalization &
Convention Enforcement"). This document is the canonical spec; the issues are for tracking.

## Context

Jig spawns Claude Code agents that write code in parallel, each in its own worktree. Several quality
controls have been built piecemeal, but they don't add up to a coherent notion of "code quality," and
nothing measures it over time:

- **Per-worktree gate** — `_auto_lint()` in `jig/worktree.py` runs `ruff format` + `ruff check` before each
  commit. Style only.
- **Canonicalization (#17, merged via PRs #19/#20/#21)** — conventions injection into every agent prompt
  (`load_conventions` → `_conventions_section`), a canonicalizer agent + `canonicalize` workflow/work-type
  defaults, a formatters config, Semgrep rule *discovery* + deprecations (`jig/canonicalize.py`), an
  `AuditStore` and `CanonicalizationIssueStore`, escalation routing, coverage/idempotency checks, and a
  per-merge mode. **However:** `semgrep` is not a dependency and is absent from the Docker/bwrap sandbox, and
  no rule content ships — so the Semgrep path is plumbed but inert.
- **Radon quality signal (PR #107, merged)** — `jig/code_metrics.py` computes `ChangeMetrics` (max cyclomatic
  complexity, ruff finding count, LoC delta) per commit and injects them into LLM reviewer prompts; flag at
  CC > 10; signal-only.
- **Reviewer federation** (`jig/reviewers/`) — mechanical + LLM-driven reviewers over agent output.

Separately, the AI code-audit taxonomy (https://kenrinzero.github.io/ai-code-audit-taxonomy/) catalogues 24
"AI-shaped" Python defect patterns with detection cues, mechanisms, and ruff/bandit mappings — a ready-made
knowledge source none of the above currently uses.

## Problem

There is no unified, measurable notion of code quality for agent output, and four concrete gaps block one:

1. **The canonicalization engine is non-functional end-to-end.** Semgrep is plumbed but not installed/sandboxed
   and ships no rules, so the deterministic-detection investment produces nothing.
2. **AI-shaped defects are undetected.** The taxonomy's 24 patterns (and the security/bugbear rules ruff could
   enforce) are not wired into anything that runs on agent output.
3. **Findings have only two fates — autofix or escalate-to-issue.** Low-confidence, not-safely-autofixable
   patterns (most AI defects) fit neither, so they are silently dropped rather than surfaced for judgment.
4. **Quality is not measured or attributable.** There is no comparable signal recorded over time, so we cannot
   tell whether a change to a prompt, role, or workflow made output better or worse — which is the actual goal.

## Simplest possible solution

Enable a handful of ruff security/bugbear rules in the worktree gate and add a sentence to reviewer prompts.
That covers a slice of (2) cheaply, but leaves the canonicalization engine inert, ships no comparable metric,
and gives no attribution — so it does not solve (1), (3), or (4). The measurement goal is the whole point, and
the simplest solution does not reach it. The honest minimum that addresses the stated problem is: make the
existing engine work, give it rules, add a reviewer disposition, and record comparable dimensions over time.

## Complications considered

- **Scale**: N/A — bounded by the changed files in a worktree per ticket; rule count is modest (24 taxonomy
  patterns + per-project rules). No non-linear growth.
- **Concurrency**: Parallel agents each work in their own worktree; quality dimensions aggregate through the
  append-only `AuditStore`, which is already single-writer-per-entry. The existing store model handles it.
- **Failure modes**: The signal must never block a commit or the dispatch path (already the `ChangeMetrics`
  contract). Semgrep/ruff/git failures degrade to empty/zeroed signal with a logged warning — never an
  exception into the pipeline.
- **Cross-cutting policies**: The taxonomy's Security category maps to security rules; the metrics themselves
  carry no PII/secrets. Sandbox is load-bearing — Semgrep must run inside Docker/bwrap where agents and the
  canonicalizer execute.
- **Cost/latency**: Deterministic passes are cheap and scoped to changed files; Semgrep adds some latency but
  is bounded to the diff. Acceptable for end-of-ticket/canonicalize cadence.

## Constraints

- Build on the merged components (`jig/canonicalize.py`, `AuditStore`, `CanonicalizationIssueStore`,
  `code_metrics.ChangeMetrics`, the reviewer federation). Do not rebuild them.
- New runtime dependencies require explicit approval — `semgrep` (OSS CLI, LGPL-2.1, free) is the one needed.
- Signal-only for low-confidence AI defects; never block commits on them.
- Must run inside the Docker + bubblewrap sandbox.
- The deterministic ruleset must be **jig-owned and curated** so the signal is comparable across any target
  repo, independent of that project's own ruff/semgrep config.
- No production deployments exist; stored data is regenerable, so no migration scripts are needed.

## Requirements

- A deterministic, comparable code-quality signal computed on agent output, consistent across target repos.
- All 24 taxonomy patterns catalogued with a routing decision (`ruff` | `semgrep` | `judgment`) and an owning
  reviewer.
- Semgrep functional inside the sandbox, invoked by the canonicalizer over discovered rules, results recorded.
- A reviewer-facing disposition: findings that are neither autofixed nor escalated can be surfaced into the
  matching reviewer's prompt.
- Quality dimensions (complexity, ruff/semgrep/taxonomy hit-rates, canonicalization activity) recorded over
  time in `AuditStore` and queryable such that a delta can be attributed to a prompt/role/workflow change.

## Non-goals

- Autofixing low-confidence AI-defect patterns (signal-only for those).
- Semantic refactoring or choosing between reasonable alternatives.
- Replacing human or LLM review.
- Non-Python languages in this initiative.
- A real-time dashboard — a CLI report (`jig audit report`) is sufficient for v1.

## Success criteria

- After a run, a comparable quality metric exists per ticket/run, recorded in `AuditStore`.
- All 24 taxonomy patterns are routed; the deterministic subset fires (ruff or Semgrep) and maps to taxonomy
  IDs.
- Semgrep runs in-sandbox and produces audit entries from the shipped rule-pack.
- At least one finding type surfaces into a reviewer prompt via the new disposition.
- `jig audit report` can show a quality delta across a prompt/role/workflow change.

## Open questions

- [ ] Is the quality "score" a single composite, or a vector of dimensions reported side-by-side (as the eval
      harness already does for static metrics)? (Likely vector; decide at design.)
- [ ] Is the eval-coverage scoreboard (how often agents emit patterns vs. how often the federation catches
      them) part of this initiative or a follow-on? (Currently optional / sub-issue #112 stretch.)
- [ ] Attribution granularity: per-ticket, per-run, or per (prompt, role, workflow) cell?

## Change log

- 2026-05-28: Initial draft (Brent Hoover)
- 2026-05-28: Approved; status draft → active (Brent Hoover)
