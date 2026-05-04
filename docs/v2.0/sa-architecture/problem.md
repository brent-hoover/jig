---
title: SA Contracts — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-04-30
---

# SA Contracts — Problem Statement

## Context

Today the SA (System Architect) role in jig is light: it picks a project template at scaffold time and gets out of the
way. Architectural decisions either don't get made (operator falls into the template's defaults) or get made implicitly
by whichever dev agent first encounters the question.

The PO/spec workflow produces a brief enumerating capabilities, behaviors, and AC. The brief is user-facing — it says
what the product does, not how the system is built. It says nothing about db schemas, API shapes, ownership of
resources, event flow, auth, or any of the other things that have to agree across modules for the system to actually
work.

The multi-level spec design (`docs/multi-level-spec/`) covers **suite** (operator-facing functional grouping) but
explicitly defers **module** (SA-owned architectural unit) and **contract** (SA-authored integration constraint) to a
separate design — this one.

## Problem

Tenet 1 of jig says we want bite-sized work *and* a coherent whole. The bite-sized half is tractable: levels of
resolution, per-suite briefs, AC fencing one behavior at a time. The coherent half is the open problem.

**Decomposition is easy; coherence under decomposition is hard.** When two dev agents work on different tickets in the
same codebase, they need to agree on shared things — schemas, APIs, event payloads, auth model, ownership of resources.
Without explicit, written contracts, each agent infers the contract from context, and two agents will infer differently.
The result is silent divergence: each ticket passes its own tests, but the system as a whole doesn't fit together.

**Concrete example (the SEO-focused catalog search service):**

- Suite "catalog" has capabilities for ingesting customer catalogs and proposing SEO-friendly category structures.
- Without contracts, Agent A (working on ingestion) might write the normalized product record one way; Agent B (working
  on categorization) might read it expecting a different shape.
- Both pass their own behavior AC. Neither knows the other exists. The integration is broken, and the failure mode is
  "categorization silently produces nonsense."

The PO brief doesn't catch this — it's user-facing. The dev agent can't catch it — its context is one ticket. Code
review can't catch it — the reviewer doesn't have a stated contract to compare against.

A second class of problem the PO workflow doesn't address: **unknown architectural unknowns.** Will SEORank's API be
fast enough? Can our db handle 100k-product catalogs? Does Shopify's delta-sync API give us what we need? These are
risks that only get answered by trying — and trying without a bounded scope is how you produce a half-built system
before discovering the foundational assumption was wrong.

What's missing is an **architectural layer** that:

1. Names integration boundaries.
2. States contracts at those boundaries (data shapes, APIs, ownership, lifecycle, cross-cutting policies).
3. Identifies architectural risks and proposes bounded spikes to de-risk them before full implementation.
4. Gets enforced by code review against the diff.

## Constraints

- **Must scale with project size.** A todo app shouldn't need a full architecture document; an ATS or hosted search
  service should. SA agent uses judgment — for trivial projects it produces a 5-line architecture.yaml and gets out of
  the way.
- **AI-driven authorship** — the SA is an agent, not a human form-filler. Operator confirms; doesn't author.
- **Must coexist with the multi-level spec (L0 → L4).** PO discovery produces capabilities; SA discovery produces
  contracts. The two workflows must compose, not compete.
- **Sequential, not concurrent.** PO discovery completes (or reaches a "done for now" gate) before SA discovery fires.
  Same shape on iteration: PO delta → SA delta → tickets resume.
- **Iterative.** Discovery doesn't complete in one pass. The architecture document is append-mostly with first-class
  versioning.
- **Tenet 4 (structured language).** Contracts live as schema- shaped YAML so they read to agents as immutable
  contracts, not drafts.
- **Tenet 1 (bite-sized + coherent).** The SA work itself has to be bite-sized — one module's contracts at a time, one
  spike at a time — even though its goal is system-wide coherence.

## Requirements

- A new architectural artifact set:
  - `architecture.yaml` at project level (cross-module, cross-cutting concerns, risk register).
  - `modules/<m>/contracts.yaml` per module (module-internal
    + boundary contracts).
- A new ontology distinction: **suite** (operator-facing, organizational) vs **module** (SA-owned, architectural). One
  suite's capabilities may be implemented across multiple modules and vice versa. (Already landed in `ontology.md`.)
- A new layer of acceptance criteria: **integration AC** (SA- authored, attached to behaviors) alongside the existing
  **behavior AC** (PO-authored).
- A **risk register** as a first-class part of `architecture.yaml`, with a defined path from "risk identified" → "spike
  proposed" → "spike executed" → "risk mitigated or accepted".
- SA is a *discovery loop*, not a one-shot. Same shape as L1 discovery — walks an axis (modules / integration
  boundaries), produces an append-mostly artifact, gates on operator approval, carries open questions and open risks
  forward.
- A code review enforcement layer (separate design): a reviewer agent reads the contracts, reads the diff, decides
  whether the change abides.
- A defined escalation path when a dev agent hits an unspecified contract mid-ticket: gap is surfaced, SA fires for
  delta amendment, dev resumes.

## Non-goals

- Full enterprise architecture documentation (ADRs, decision logs, C4 model). The SA artifact is purpose-built for agent
  consumption, not for org-wide engineering review.
- Cross-suite dependency tracking at fine grain. Module-level ownership and boundary contracts are enough; we don't need
  a full call graph.
- Replacing tools like OpenAPI / JSON Schema / Protobuf. Where those are already in use, the contract references them;
  we don't reinvent.
- Authoring code modules / running scaffolders against the contracts. The SA outputs the contracts; scaffolding /
  implementation is downstream.
- Specifying *implementation details* (which dedup algorithm, which library version, which sort order). That's behavior
  AC and ticket-level decision.
- Replacing human architectural review. The SA agent + reviewer agent help; they don't substitute for an operator who
  knows what they're building.

## Success criteria

- For a medium-size project (15-25 capabilities, 4-6 suites), an operator can run SA discovery after PO discovery
  completes and end up with: a project-level `architecture.yaml`, a `contracts.yaml` per module, integration AC layered
  onto the behavior AC in the suite briefs, and a risk register with at least the highest-impact unknowns flagged.
- A dev agent working on a ticket sees both behavior AC and integration AC and can tell its work is "done" against both.
- A reviewer agent can mechanically check a diff against the relevant contracts and either approve or flag specific
  contract violations.
- A new ticket that surfaces a contract gap can escalate to SA, get the contract amended, and resume — without operator
  having to manually mediate every interaction.
- Risks that warrant spikes get spike tickets generated; spike outcomes feed back into the architecture as either
  mitigation or accepted-risk.
- For a small project (a CLI tool, a single-suite todo app), SA produces a minimal architecture.yaml and gets out of the
  way — no friction.
