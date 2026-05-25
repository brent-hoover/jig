---
title: Add ruff checks to dev completion phase — Problem Statement
type: problem
status: draft
owner: Brent Hoover
created: 2026-05-25
updated: 2026-05-25
---

# Add ruff checks to dev completion phase — Problem Statement

## Context

Jig workflows run automated checks at phase boundaries to gate agent progress. The current check
catalog includes `ruff-check` as a required check, but it is only wired into the `validate`
phase — the final phase of every workflow. Dev and test agents can commit and resolve their
tickets without their code passing ruff.

## Problem

Ruff violations written by dev or test agents pass through the implement and review phases
undetected. They surface at validate, which bounces the ticket back to dev, triggering a full
extra dev → review → validate cycle. This is both expensive (the hn-cli eval traced 54% of
total run cost to one ticket where this pattern compounded) and incoherent — the validate phase
is not the right place to catch lint errors that should have been caught at write time.

## Simplest possible solution

Add `ruff-check` to the `automated_checks` list on the `test` and `implement` phases in every
workflow that has those phases. Add a one-sentence note to the `dev` and `test` role prompts
telling agents that ruff must pass before they resolve the ticket.

## Complications considered

- **Scale**: N/A — checks run per-ticket in isolated worktrees; no growth effect.
- **Concurrency**: N/A — checks run per-agent in isolated worktrees; no shared state.
- **Failure modes**: If ruff fails at the implement/test gate, the agent is bounced back
  immediately — coherent and cheap. The current failure mode (caught only at validate) is the
  incoherent expensive path; this change eliminates it.
- **Cross-cutting policies**: N/A — touches none.

## Constraints

- None.

## Requirements

- `ruff-check` must be listed under `automated_checks` for every `test` phase and every
  `implement` phase in the default workflow catalog.
- `dev.yaml` and `test.yaml` role prompts must state that ruff must pass before resolving the
  ticket, so agents know the gate exists and can fix violations proactively.

## Non-goals

- None identified.

## Success criteria

- Ruff violations are caught at the implement or test gate, not at validate.
- No extra validate-triggered cycles due to lint errors in normal runs.

## Open questions

- None.

## Change log

- 2026-05-25: Initial draft (Brent Hoover)
