---
title: Deterministic Ticket Spec — Problem Statement
type: problem
status: active
owner: brent
created: 2026-05-23
updated: 2026-05-23
---

# Deterministic Ticket Spec — Problem Statement

## Context

`jig init` produces a structured YAML project spec (`project.structured.yaml`) from the operator's brief. Each
capability in that spec carries machine-readable acceptance criteria. When tickets are created from capabilities, they
carry a `derived_from` pointer back to the originating capability in the spec.

Before the test phase of a medium+ ticket, the `spec` role runs. Its current job, per its role definition, is to
"draft a design document... in markdown format." The spec agent improvises a prose design doc and commits it. The
test agent then reads that markdown and decides what to test.

## Problem

The per-ticket spec agent is re-deriving information that already exists in authoritative, structured form in
`project.structured.yaml`. It expresses that information as freeform markdown, losing precision at every step:

- The AC in the project spec is structured YAML with discrete, enumerable assertions.
- The spec agent's markdown re-states those assertions in prose, introducing ambiguity and omission.
- The test agent must re-interpret the prose to decide what to cover, with no machine-readable contract to
  enforce completeness.

The result is what the eval run confirmed: reviewers flag missing coverage as `notable` advisories rather than
verifiable failures, because there is no authoritative spec to check against.

The brief → project spec pipeline is already deterministic: `spec_generate_from_brief` is a pure function with no
LLM in the hot path. The per-ticket spec step is the opposite — a full agent run producing an artifact of lower
fidelity than what already exists in the project spec.

## Simplest possible solution

A `spec_materialize_from_project` tool that:
1. Reads `project.structured.yaml`
2. Looks up the capability matching `ticket.derived_from`
3. Writes the capability's AC to `.jig/spec/tickets/{ticket_id}.yaml`

The `spec` role calls this tool. The agent's only remaining non-deterministic work is an optional semantic gap pass
(same pattern as `spec-generator`): check if the ticket's AC fields add anything beyond what the project spec
captured, and flag gaps. If no gaps, it exits immediately.

The test agent is updated to consume `.jig/spec/tickets/{ticket_id}.yaml` as its test contract.

## Complications considered

- **Scale**: N/A — one project spec, one capability lookup per ticket. Bounded.
- **Concurrency**: N/A — spec phase is single-writer; runs before test and dev.
- **Failure modes**: If `derived_from` is missing or points to an unknown capability, fail loudly and block the
  ticket — same pattern as a missing AC. A ticket without a derivable spec cannot proceed.
- **Cross-cutting policies**: N/A.
- **Tickets not derived from project spec**: Some tickets (chores, bugfixes) may not have a `derived_from` pointing
  at a capability. These don't run the `default` workflow today (they use `feature-xs` or `bugfix`), so the spec
  phase doesn't fire. No change needed for those.
- **Ticket AC extending the project spec**: The PM may add ticket-level AC beyond what the project spec captured.
  The materialized spec should include both: project spec capability AC as the base, plus any ticket-level additions.

## Constraints

- Must not require the operator to re-run `jig init` or modify the project spec manually.
- The YAML schema for the ticket spec should be a strict subset of / derived from the project spec capability shape,
  so downstream consumers (test agent, reviewers) can rely on a stable contract.
- The `spec` role prompt change must be backward-compatible for projects that predate this feature (i.e., no project
  spec present) — fall back to current behavior or fail loudly with a clear message.

## Requirements

- The ticket spec is materialized from `project.structured.yaml` without LLM involvement.
- The materialized spec is written to a known, stable path resolvable from the ticket ID.
- The test agent is told to drive coverage from the materialized spec, with every AC item requiring at least one test.
- If `derived_from` is absent or unresolvable, the spec phase fails loudly rather than falling back to prose.
- The spec role prompt no longer instructs the agent to write a markdown design doc.

## Non-goals

- Replacing the project-level `spec-generator` (that pipeline is correct as-is).
- Adding new AC fields at the ticket level through the spec agent (the PM owns AC).
- Migrating existing projects that have hand-written markdown design docs.
- Making the test agent automatically generate tests from the YAML (it still writes tests; it just has a better
  contract to work from).

## Success criteria

- The spec phase for a medium+ ticket completes with zero LLM turns (or one short gap-check turn) instead of a
  full agent run.
- The test agent's coverage can be checked against the materialized YAML spec mechanically.
- RC-7-style findings (missing coverage of a specific AC item) become detectable failures rather than advisory
  reviewer opinions.
- Eval runs show reviewer-test-adequacy findings drop in count and severity for tickets with a well-formed project
  spec.

## Open questions

- [x] What is the target YAML schema for the materialized ticket spec? **Mirror the capability shape from
      `project.structured.yaml` exactly. The agent also has access to the full project spec for context during
      the gap pass.**
- [x] Should the spec phase be eliminated as an agent and replaced entirely with a pre-phase hook? **Keep the
      agent wrapper. If the gap pass never fires during evals we can drop it later.**

## Change log

- 2026-05-23: Initial draft (brent)
