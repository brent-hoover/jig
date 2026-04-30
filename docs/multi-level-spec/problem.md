---
title: Multi-level Spec — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-04-30
---

# Multi-level Spec — Problem Statement

## Context

Today `jig init` runs a single PO conversation that produces one
brief at `.jig/spec/project.md`, with all capabilities, behaviors,
non-goals, and AC enumerated in one document. Spec-gen produces one
structured projection. SA picks one template. Done.

That works for projects where the operator can hold the whole product
shape in their head in one sitting — a todo app, a CLI tool, a small
SaaS. The brief comes out at 5-10 capabilities and the conversation
stays grounded.

## Problem

Two related symptoms appear when the project is bigger than that:

1. **The single PO conversation becomes unwieldy.** For a hosted
   search service for ecommerce, an applicant tracking system, or any
   medium-complexity product, there are 15-25 substantive capabilities
   spanning multiple subsystems. The operator can't enumerate them all
   in one conversation — they need to discover the shape iteratively,
   walking through who uses the product before they know what it does.
   Trying to do that in a single brief produces either:
   - Truncated coverage (only what the operator thought of in 30 minutes), or
   - Premature commitment (forcing names + AC on capabilities the
     operator doesn't yet understand).

2. **No "broad sketch first, drill down later" affordance.** AI-driven
   development needs explicit hierarchy: at any given level of detail,
   "done" must be a verifiable boundary. Currently jig has two levels —
   project-spec (the brief) and ticket. That's too coarse. A model
   working a ticket has no intermediate "module" context to ground in;
   a model writing the brief has to fully spec everything or nothing.

## Constraints

- Small-to-medium scope. Not enterprise. ATS or hosted ecommerce search
  is the canonical "medium." Roughly 15-25 capabilities, 4-6 modules.
- Must coexist with the existing simple-brief flow. Today's flow
  becomes the *module-level* flow at the new bottom of a hierarchy.
- AI-driven authorship throughout: the workflow leans on PO agents,
  not human form-filling.
- Iterative: operator can stop / resume at any level. New journeys
  added to L1 must be able to propagate to L2 / L3 with operator
  approval.

## Requirements

- Multiple resolution levels with explicit "done" boundaries:
  L0 pitch, L1 discovery, L2 modules, L3 module brief, L4 tickets.
- Discovery (L1) is journey-driven — every capability must be
  traceable to a persona's journey ("no functionality outside a journey").
- Personas are bounded: customer, merchant, maintainer, plus
  domain-specific extras. The "system" has no journey of its own —
  platform/maintenance work is a maintainer journey.
- Federated structured spec — the project-level spec is small (modules
  + crosscutting non-goals); each module has its own structured spec.
- New TUI / CLI affordances to drive the multi-level workflow without
  introducing operator confusion.

## Non-goals

- Enterprise scale (50+ capabilities, multi-team org charts).
- Replacing the simple-brief format. The existing format keeps working
  at L3 (module level).
- Cross-module dependency tracking. Out of scope for v1.
- Multi-version planning (v1 vs v2). Out of scope for v1.

## Success criteria

- An operator can `jig create <name>` for an ATS-sized product, walk
  through L0 + L1 + L2 in one sitting, and end up with a coherent
  module list — without enumerating individual capabilities.
- For each module, `jig module init <name>` runs the existing simple-
  brief flow scoped to that module.
- Adding a persona later (`/journey add <persona>`) proposes
  module/capability deltas the operator can accept.
- spec-gen at the project level produces a federated structured spec;
  per-module spec-gen produces a module-scoped structured spec.
