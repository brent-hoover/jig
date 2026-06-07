---
title: Quality Feedback Loop — Problem Statement
type: problem
status: active
owner: Brent Hoover
created: 2026-06-02
updated: 2026-06-06
---

# Quality Feedback Loop — Problem Statement

## Context

Jig has two quality mechanisms that were built in recent epics but have not been connected to
agent behavior:

1. **`QualitySnapshot` store** — `dispatch_with_llm_spawn` records a per-cycle snapshot of
   objective code metrics (max cyclomatic complexity, ruff findings, LoC delta, taxonomy hit
   counts) before reviewer agents are spawned. The data exists in `quality_snapshots.jsonl` but
   nothing reads it after the fact.

2. **`_code_metrics_section` in `prompt_builder.py`** — reviewer prompts already include an
   "Objective Code Metrics" block with max CC, ruff count, and LoC delta. Taxonomy hits are also
   injected, but only for entries whose `owning_reviewer` field matches the spawned reviewer's
   role. In small-profile runs (only `reviewer-generalist` spawned), no taxonomy entry claims
   `reviewer-generalist` as its owner, so the generalist receives the universal metrics block but
   zero deterministic taxonomy findings — the entire taxonomy signal is silently unrouted.

3. **`FindingAcksStore` + `apply_severity_disposition`** — the orchestrator tracks whether the
   dev acknowledged each finding (`addressed`, `resolved`, `reraised`). But the severity
   disposition has a gap: when a ticket has both `important` and `notable` comments,
   `has_higher_severity = True`, `coord_arg = None`, and the notables are silently dropped — they
   never reach the DEFERRED queue and are never carried forward to the dev re-run. The dev's
   obligation is to fix the blocking (important) finding; the notable is gone.

## Problem

Two distinct failure modes compound to let quality defects escape:

**Failure 1 — Taxonomy hits are silently unrouted in single-reviewer dispatches.**
In any run that spawns `reviewer-generalist` instead of the specialist federation, the
deterministic taxonomy findings (error-handling patterns, language pitfalls, etc.) are filtered
out of the reviewer's prompt because the filtering code matches hits to their `owning_reviewer`.
The generalist owns no taxonomy entries. The scanner ran, found signals, recorded them in the
snapshot — and then the reviewer never saw them. In the hn-cli eval run this manifested as 25
`language-pitfall` hits and 1 `error-handling` hit being recorded but invisible to the
`reviewer-generalist`. The `error-handling` hit happened to correlate with an RC-1 block the
reviewer independently caught; the language-pitfall hits produced no findings at all.

**Failure 2 — Notable findings are never shown to the dev.**
`build_fix_loop_bundle` (line 73) filters to `severity in ("critical", "important")` only. This
means: (a) when a ticket has only notable findings, the bundle returns empty and the dev receives
no findings context at all; (b) when a ticket has both important and notable findings, the dev's
re-run bundle contains only the importants — the notables are excluded. The dev cannot call
`mark_finding_addressed` for a finding it never sees. In the hn-cli eval this produced a live
`--name TEXT / "Hello, world!"` scaffold placeholder in the shipped CLI surface after two review
cycles: the reviewer flagged it twice, the dev never received it in its prompt.

The combined effect: the system has the data (snapshot, taxonomy hits, finding acks) but no path
from data → reviewer awareness → enforced resolution.

## Complexity drivers

- **Scale**: N/A — both failures are per-ticket, not load-driven.
- **Concurrency**: N/A — the fix is in per-ticket sequential paths; no new writers introduced.
- **Failure modes**: Moderate. Quality defects that the deterministic scanner or reviewer
  explicitly flagged can ship unaddressed — scanner hits are silently unrouted and reviewer
  notables are silently dropped. These are real code-quality escapes to the shipped artifact,
  not theoretical risks.
- **Cross-cutting policies**: N/A — no PII, auth, or secrets involved.

## Constraints

- The reviewer prompt injection must not break existing specialist-federation runs (runs that
  DO spawn specialist reviewers should continue to get filtered taxonomy hits per their role).
- Notable findings must remain non-blocking — a ticket with only notables still resolves. But
  the dev must call `mark_finding_addressed` for every finding (including notables) before
  resolving, making acknowledgement machine-verifiable.
- The fix must work within the existing `FindingAcksStore` and `apply_severity_disposition`
  contract — no schema changes to existing stores.
- No edits to existing reviewer role YAML files (the fix must be in the prompt-building code, not
  in role configs).

## Requirements

- Taxonomy hits flagged by the deterministic scanner must reach at least one spawned reviewer's
  prompt. A hit recorded in the snapshot but not present in any spawned reviewer's prompt
  is a silent drop and must not occur.
- All findings — `critical`, `important`, and `notable` — must appear in the dev's fix-loop
  bundle. The dev must call `mark_finding_addressed` for every finding before the ticket can
  resolve.

## Non-goals

- Changing the severity semantics of `notable` — it must remain non-blocking.
- Implementing a new reviewer type or splitting `reviewer-generalist` into specialist sub-roles.
- Building a cross-run quality dashboard or trend analysis on `QualitySnapshot` data.
- Auto-applying notable findings that have a `suggested_diff` (a separate decision; out of scope
  here).
- Changing the `QualitySnapshot` store schema.

## Success criteria

- In a small-profile eval run with only `reviewer-generalist` spawned, all taxonomy hits from the
  scanner appear in the reviewer's prompt regardless of `owning_reviewer`.
- In any eval run where a reviewer flags a `notable`: the notable appears in the dev's fix-loop
  bundle alongside any importants, and the dev produces a `FindingAcks` record of kind
  `addressed` for it before resolving. No finding exits the ticket without an ack.
- No regression in runs that use the full specialist federation: specialist reviewers still
  receive only their own taxonomy hits (plus the universal block), not the full unfiltered list.

## Open questions

None.

## Change log

- 2026-06-02: Initial draft (Brent Hoover)
