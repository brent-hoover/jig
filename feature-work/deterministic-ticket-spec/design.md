---
title: Deterministic Ticket Spec — Design
type: design
status: active
owner: brent
created: 2026-05-24
updated: 2026-05-24
problem: ./problem.md
---

# Deterministic Ticket Spec — Design

## Summary

Extend `handle_create_ticket` in `ticket_mcp.py` to write a `TicketSpec` inline when the new ticket has a
`derived_from` URI pointing at a capability in `project.structured.yaml`. The capability's AC fields are
materialized into the `TicketSpec` at creation time — no separate spec phase, no spec agent, no new MCP tool.
The `test` role is updated to treat the materialized spec as its authoritative test contract.

## Approach

### 1. Spec materialization in `handle_create_ticket`

After the ticket is created and before the bus event is published, `handle_create_ticket` checks:

1. Does `args` include a `derived_from` value matching `project://spec/capabilities/{id}`?
2. Does `project.structured.yaml` exist at `project_path`?

If both are true:
- Parse the capability ID from the URI.
- Load the `StructuredSpec` via `spec_loader.load_structured_spec(project_path)`.
- Look up the capability via `spec.capability_by_id_or_alias(cap_id)`.
- Build a `TicketSpec` with `fields` populated from the capability:
  - `summary` ← `capability.summary`
  - `behaviors` ← `capability.behaviors` (serialized as a list of dicts with their AC)
  - `acceptance_criteria` ← `capability.acceptance_criteria` (top-level, if any)
- Write it via `save_ticket_spec(project_path, ticket_spec)`.

If either condition is false, creation proceeds as today — no spec is written, no error raised. This is
best-effort enrichment, not a gate.

The ticket's `derived_from` field is also set on the `Ticket` record from `args["derived_from"]` so downstream
code (context resolver, reviewers) can trace the lineage.

### 2. `spec` phase removed from `default` workflow

The `default` workflow loses its `spec` phase. The sequence becomes:

```
test → review-tests → implement → review → validate → document
```

The `spec` role definition is left in place (projects may still define their own workflows that use it), but
the shipped `default` workflow no longer references it.

### 3. Updated `test` role

The test role gains `ticket://spec` in its `default_context`. It is told:

- Read the spec at the start. Every item in `behaviors[*].acceptance_criteria` and top-level
  `acceptance_criteria` is a required test target.
- Test names should map clearly to AC items.

If no spec file exists (ticket not derived from a capability), the test role falls back to the ticket
description as today.

### 4. `derived_from` accepted by `create_ticket`

`handle_create_ticket` already reads arbitrary `args`. Adding `derived_from` as a recognised optional field
requires no schema change — just `args.get("derived_from")` set on the ticket kwargs. The PM role prompt gains
a note to pass `derived_from` when creating tickets from project spec capabilities.

## Interfaces

No new files, tools, or APIs. Changes are internal to `handle_create_ticket` and the `default` workflow YAML.

The `TicketSpec` written at creation time uses the existing schema and path convention
(`.jig/specs/{ticket_id}.yaml`) — identical to what a spec agent would have produced. Downstream consumers
(`ticket://spec` context resolver, `reviewer-test-adequacy`) are unaffected.

## Data model

No changes. `TicketSpec` already exists in `jig/specs.py`. The `Ticket` model already has
`derived_from: str | None`. Both stores already have their file layout and CRUD operations.

## Alternatives considered

### New `spec_materialize_from_project` MCP tool + spec agent

A new MCP tool called by a thin spec agent at the start of the spec phase. Rejected: the spec phase costs
agent turns even when there's nothing to decide, and xs/s tickets (which skip the default workflow) never get
a spec. `create_ticket` fires for all ticket sizes and has all the information available inline.

### Pre-phase hook before `test`

A deterministic hook before the test phase, without any agent. Cleaner than the spec agent but still defers
to workflow execution time, leaving xs/s tickets without a spec.

### Chosen: inline in `create_ticket`

Simplest possible location. All information is present, the operation is a pure lookup-and-write, and it fires
for every ticket regardless of workflow. No new abstractions required.

## Risks

- Loading `project.structured.yaml` on every `create_ticket` adds a file read. Specs are small (tens of
  capabilities); acceptable.
- If `derived_from` doesn't match any capability, log a warning and continue — ticket created without a spec
  rather than failing hard.

## Out of scope

- Updating `reviewer-test-adequacy` to mechanically verify AC coverage against the spec (valuable follow-on).
- Populating `TicketSpec` for tickets not derived from the project spec (bugfix, chore, etc.).
- The semantic gap pass originally planned for the spec agent. Deferred indefinitely.

## Open questions

None — ready for implementation.

## Change log

- 2026-05-24: Revised — moved from spec-agent+MCP-tool to inline create_ticket materialization (brent)
