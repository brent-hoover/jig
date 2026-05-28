---
title: Radon Code-Quality Signals — Problem Statement
type: problem
status: active
owner: Brent Hoover
created: 2026-05-25
updated: 2026-05-28
---

# Radon Code-Quality Signals — Problem Statement

## Context

Jig agents produce code that passes ruff and tests but may still be a mess — sprawling functions,
high cyclomatic complexity, or structural debt that no linter catches. The only automated quality
gate on a worktree before commit is `_auto_lint()` in `jig/worktree.py`, which runs ruff format
and ruff check.

`radon>=6.0.1` is already in the project's dependency list (added for `evals/prompt-style-eval/`),
so the tooling cost is already paid.

## Problem

There is no deterministic, objective code-quality signal on what agents produce. Reviewers
in `jig/reviewers/` make LLM-based judgments without numeric grounding. The worktree lint stage
catches style violations but not structural complexity. "This passes but the code is a mess" is
currently invisible to both the commit gate and the reviewer prompts.

## Simplest possible solution

After the ruff pass in `_auto_lint()`, run `radon cc` on changed Python files. Log the max
cyclomatic complexity score. Surface that number in the reviewer prompt block so the LLM reviewer
has an objective data point. No blocking — just signal.

## Complications considered

- **Scale**: N/A — bounded by the set of changed files in a single worktree commit; linear and small.
- **Concurrency**: N/A — `_auto_lint` is called once per commit, single-writer per worktree.
- **Failure modes**: If radon errors (e.g., syntax error in generated code), it should not block
  the commit — degrade gracefully, log the failure, surface no CC metric. A failed radon run is
  less important than a blocked commit.
- **Cross-cutting policies**: N/A — touches no PII, auth, secrets, or external services.

## Constraints

- `radon` must already be in deps (it is: `radon>=6.0.1`).
- No new dependencies may be introduced.
- Radon failures must not block commits — signal only, not gate.
- Must work inside the Docker/bwrap sandbox environment where agents run.

## Requirements

- `_auto_lint()` (or a companion function it calls) computes max cyclomatic complexity for changed
  Python files after each ruff pass.
- The complexity result (max CC score + which function/file hit it) is returned to the commit
  caller and made available to the reviewer dispatch path.
- Reviewer prompts include a small metrics block: max CC, ruff finding count, LoC delta.
- Radon failures are non-fatal: logged, no metric surfaced, commit proceeds.

## Non-goals

- Blocking commits on high CC (flag only).
- Setting a specific CC threshold policy in this feature (threshold TBD at design time).
- Covering non-Python files.
- Replacing any existing lint or reviewer behavior.

## Success criteria

- After a worktree commit, the orchestrator/reviewer has access to a CC score for the changed code.
- At least one reviewer prompt includes the numeric metrics block.
- Radon errors do not block or error out the commit pipeline.
- No new dependencies are added beyond what is already in `pyproject.toml`.

## Open questions

- [ ] Which reviewers should receive the metrics block — all of them, or only the code-quality
      judgment reviewers (e.g., intent-compliance, cross-cutting-policy)?
- [ ] What CC threshold is worth surfacing vs. treating as noise? (Decide at design time.)

## Change log

- 2026-05-25: Initial draft (Brent Hoover)
- 2026-05-28: Approved; status draft → active (Brent Hoover)
