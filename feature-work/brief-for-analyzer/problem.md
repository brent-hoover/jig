---
title: Restore Brief for Project Analyzer — Problem Statement
type: problem
status: draft
owner: Brent Hoover
created: 2026-06-07
updated: 2026-06-07
---

# Restore Brief for Project Analyzer — Problem Statement

## Context

When a jig-orchestrated project completes, the orchestrator automatically runs the post-run analyzer
(`_run_analyzer_bg` in `orchestrator.py`). The analyzer produces `analysis.md` summarizing ticket
outcomes, agent behavior, and whether the project met its acceptance criteria. The intent is to give
the operator a quality signal without manual effort.

Every `/init`-ed project has a `docs/brief.md` at `<project_path>/docs/brief.md`. This file
contains the human-authored brief that defines the project's purpose and acceptance criteria — the
ground truth for evaluating whether the work actually solved the right problem.

## Problem

`_run_analyzer_bg` calls `analyze()` without passing `brief_path`, so `analyze()` proceeds with
`brief_path=None`. The LLM fidelity pass has no access to the brief and falls back to inferring
intent from the structured spec and ticket titles. The resulting `analysis.md` contains notes like:

> Note: `brief.md` was not included in the provided artifacts — the field is empty. The following
> is reconstructed from the spec-generator output, SA notes, and ticket titles.

and

> No silently-dropped items identified, but brief.md absence prevents confident verification of
> all acceptance criteria.

This means the analyzer cannot verify whether behavior specified in the brief survived the
PO→spec translation step — which is exactly the failure mode the analysis is meant to catch.
Observed in the `hn-cli-20260524T144910Z` analysis bundle (issue #82).

There is a second layer: the CLI entry point in `evals/watcher/analyzer.py` does attempt
auto-detection, but it looks at `<jig_repo>/evals/projects/<name>/brief.md`, which is only valid
for built-in eval fixtures. It does not look at the canonical `<project_path>/docs/brief.md`
location used by real projects.

## Complexity drivers

- **Scale**: N/A — one file path lookup per analysis run; no growth dynamics.
- **Concurrency**: N/A — single-writer, analyzer runs in a thread pool executor after the project
  completes.
- **Failure modes**: Brief may be absent at the canonical location; degraded-but-correct analysis
  (proceed without brief, note the gap) is acceptable. No data loss risk.
- **Cross-cutting policies**: N/A — brief.md is project documentation, no PII or secrets.

## Constraints

- Real projects store the brief at `<project_path>/docs/brief.md`; no caller of `analyze()`
  currently supplies it.
- When no brief is found, the current degraded-but-correct behavior (proceed without brief, note
  gap in `analysis.md`) must be preserved.

## Requirements

- When a brief exists at the project's canonical location and no explicit `brief_path` is supplied,
  the LLM fidelity pass must include the brief.
- When no brief is available from any source, current behavior is preserved.
- A regression test covers both branches.

## Non-goals

- Changing how the eval-fixture CLI auto-detection works (`evals/projects/<name>/brief.md`).
- Any other changes to analyzer inputs or outputs beyond brief inclusion.

## Success criteria

- The analyzer no longer produces "brief.md was not included" disclaimers for projects that have
  a brief at the canonical location.
- Manual re-run of the `hn-cli` eval confirms `analysis.md` no longer carries the disclaimer.
- Both the "brief found" and "brief absent" code paths have regression test coverage.

## Open questions

- [x] Should the canonical brief location be resolved relative to `project_path` as passed, or
      `project_path.resolve()`? → Use `project_path` as-passed, matching how the CLI and `llm.py`
      consume it. No `.resolve()` needed.

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
