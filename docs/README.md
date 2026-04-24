# Documentation

This directory holds all project documentation. It is organized by
feature/component so that everything about a given piece of work lives
together and can be read as a unit.

## Structure

```
docs/
  <feature>/              # most work lives here
    problem.md            # what we're solving and why
    design.md             # how we're solving it (or design-<aspect>.md)
    plan.md               # implementation plan (throwaway)
    specs/                # component specs, one per file
    notes.md              # scratch, non-canonical
  decisions/              # ADRs — numbered, dated, durable
  runbooks/               # operational guides
  reference/              # long-lived reference material
  _templates/             # copy these when creating new docs
  ARCHIVE/                # superseded docs, kept for history
```

## Document types

| Type       | Purpose                                     | Lifetime  |
|------------|---------------------------------------------|-----------|
| problem    | What we're solving and why                  | Long      |
| design     | How we chose to solve it, tradeoffs         | Long      |
| plan       | Ordered steps to implement                  | Throwaway |
| spec       | What must be true (EARS-style requirements) | Long      |
| decision   | ADR — a decision made, with context         | Permanent |
| runbook    | Operational guide for running the thing     | Long      |
| reference  | Long-lived reference (glossary, overview)   | Long      |
| notes      | Scratch space, explicitly non-canonical     | Throwaway |

Throwaway docs get archived or deleted when the work they support is done.
Long-lived docs get `status: superseded` when replaced.

## Frontmatter

Every doc begins with YAML frontmatter. Required on every doc:

```yaml
---
title: <human-readable title>
type: <one of the types above>
status: <see vocabulary below>
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

### Status vocabulary

Status values are fixed. Do not invent new ones.

- **problem, design, plan, notes, runbook, reference:**
  `draft | active | superseded | archived`
- **spec:**
  `draft | approved | implemented | verified | superseded`
- **decision:**
  `proposed | accepted | superseded | deprecated`

### Per-type extras

Most docs only need the required fields. A few types carry extras:

- **design:** `problem: <relative path to problem.md>`
- **plan:** `design: <relative path to design.md>` (or `problem:` if no design)
- **spec:** `id: REQ-<AREA>-<NUM>`, optional `depends_on: [ids]`,
  optional `implements: [paths]`
- **decision:** `id: ADR-<NUM>`, `supersedes: []`, `superseded_by: null`
- **runbook:** `service: <service-name>`

## Rules

1. New feature work starts with `docs/<feature>/problem.md`.
2. Do not create docs outside this structure.
3. Do not create generic-named docs at the top level
   (`notes.md`, `thoughts.md`, `ideas.md`, `TODO.md`, etc.).
4. Copy the appropriate template from `_templates/` when creating a new doc.
5. Update the `updated` field whenever you edit a doc.
6. Specs and ADRs are not modified without explicit instruction.
7. When a doc is superseded, set its `status: superseded` and fill in
   `superseded_by` before creating the replacement.

## Why feature-first?

Grouping by feature means everything about a piece of work — the problem,
the design, the plan, the specs, the scratch notes — lives in one folder.
When the work is done, the folder is a self-contained record. When you
return to it six months later, you open one directory and see the whole
story.

The exceptions (decisions, runbooks, reference) are genuinely cross-cutting
and don't belong to a single feature, so they live at the top level.
