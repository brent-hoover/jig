# 04 — Ownership and Owner Roles

Durable artifacts (spec, architecture document, decision records, coding
conventions, etc.) need owners. Without explicit ownership, durable
artifacts rot — the classic "curation by diffuse responsibility" failure
mode. Ownership makes curation concrete: a specific role decides what
goes in each artifact, with changes going through them.

## The general pattern

Every durable artifact has a declared owner role. Changes to the artifact
go through the owner:

1. An actor proposes a change via a Proposal thread entry.
2. The proposal is routed to the owning role.
3. The owner accepts, rejects, or requests refinement.
4. Accepted proposals are applied to the artifact, creating a new version.

Ownership is a role assignment, not a permanent attachment to a specific
person. Roles are filled by humans, agents, or humans-with-helpers
(below). The same actor can fill the owner role for multiple artifacts
on small teams.

Structural discipline: **the same actor cannot be both proposer and
acceptor on the same proposal.** This is the self-certification guard
from problem 7 applied to artifact changes. On small teams where one
person genuinely fills both roles, the harness tracks this as
"self-approval with justification" — allowed but marked in the audit
trail so the tradeoff is visible.

## Two first-class owner roles

Most software projects have two fundamentally different kinds of
decisions, and they benefit from different owners.

**Product Owner (PO).** Owns product decisions:

- What the feature should do.
- Priority and scope.
- User-facing behavior and UX.
- Business tradeoffs — "this edge case matters, this one doesn't."
- Accepting or rejecting proposals to change product behavior.

Domain: project spec (primary artifact — see [02](./02-project-spec.md)),
spec behaviors per work unit, acceptance criteria, out-of-scope
declarations, domain glossary, roadmap.

**Systems Architect (SA).** Owns technical decisions:

- How the system is structured.
- Technology and library choices.
- Patterns and conventions.
- Cross-cutting concerns (security, performance, scalability).
- Accepting or rejecting proposals to change technical approach.

Domain: architecture document, coding conventions, technical decision
records, design sections of specs, security policy (absent a dedicated
security owner).

Other owner roles can exist (security lead, QA lead, etc.) but PO and SA
are the two the harness ships with as built-in concepts. Projects
declare additional owners as needed.

## Why these two, and not more

Most decisions cleanly fall into product or technical categories. Ones
that span both (expensive features, user-facing technical changes) are
handled by routing proposals to both owners with joint resolution.

Three or four owner roles would create artificial boundaries and slow
decisions without clear benefit. One owner role would erase the
product-vs-technical distinction that genuinely matters in how teams
think about tradeoffs.

Security is a common case where teams want a separate owner. The
default is that SA inherits security concerns; teams with dedicated
security roles declare separate ownership for security artifacts.

## Jointly owned artifacts

The spec is jointly owned in practice — behaviors and acceptance belong
to PO; technical approach and risks belong to SA. The ownership map can
be artifact-level (one owner for the whole thing) or section-level
(different owners for different sections).

Section-level ownership in the spec:

```yaml
owner_product: po
owner_technical: sa
behaviors: [...]           # PO owns
acceptance_criteria: [...] # PO owns
design: [...]              # SA owns
technical_risks: [...]     # SA owns
```

Proposals targeting sections route to that section's owner. Proposals
affecting multiple sections may route to both owners, requiring joint
acceptance.

## Proposals as thread entries

A Proposal is a new thread entry type. It carries:

- Target artifact (and section, if section-level ownership).
- Proposed change, specific enough to apply if accepted.
- Rationale.
- Target owner(s) — derived from ownership map; declared explicitly if
  overriding.
- State: pending / accepted / rejected / refining.

The owner resolves a Proposal by:

- **Accepting** — the change is applied to the artifact; a new version
  is recorded; the proposal is closed.
- **Rejecting** — with reasoning. The rejection is recorded (rejected
  proposals with reasoning are valuable — they prevent re-litigation).
- **Refining** — requesting changes. The proposer revises; the owner
  reviews again. Loop until accepted, rejected, or abandoned.

Resolution asymmetry applies: the proposer cannot self-accept.

Proposal is the eleventh thread entry type, joining Question, Answer,
Objection, Resolution, Waiver, Decision, Handoff, Escalation,
Uncertain, and Note. See [08 — Threads](./08-threads.md).

## Multi-owner proposals

When a proposal spans product and technical domains (e.g., "skip
deletion feature because it's technically expensive"), both owners are
targeted. Resolution requires acceptance from all listed owners.

If they disagree, the proposal enters refining state — a back-and-forth
in the thread between owners to reach consensus. If consensus proves
unreachable, the proposal can be escalated to a human decision-maker
(usually a lead or someone with authority over both). The escalation
path is declared per project.

Deadlock handling applies (from [08](./08-threads.md)) — if owners fail
to converge within a configured window, the orchestrator escalates.

## Human-with-helper assignment

PO and SA roles are the ones most likely to benefit from being human
with LLM assistance rather than autonomous agents. Both require
judgment that's hard to give an agent well:

- PO needs market understanding, user empathy, stakeholder awareness.
- SA needs deep codebase knowledge, historical context, long-term
  strategic thinking.

An agent filling these roles works for routine decisions (small spec
changes, minor technical choices) but degrades on high-stakes ones. The
sweet spot is human-with-helper: human retains authority; agent helps
them move faster by drafting recommendations, summarizing alternatives,
checking consistency.

New assignment type for these roles:

- `human_with_helper: <role>, helper_template: <template>` — a named
  human fills the role; an agent template provides advisory drafts.
  The human makes the decision; the helper's recommendation is
  recorded for reference.

When a proposal is routed to a human-with-helper owner, the harness
spawns the helper agent first. The helper produces a recommendation (as
a thread Note or advisory entry). The human sees both the proposal and
the helper's recommendation, then accepts/rejects/refines. Helper
recommendation and human decision are both recorded.

This is a different mode from "agent is the owner" and deserves its own
assignment type so it's visible in the audit trail which owners had
human authority and which didn't.

## Assignment patterns per role

Recommended defaults:

- **PO**: human-with-helper when a human is available; agent fallback
  when not, with escalation-to-human for non-routine proposals.
- **SA**: human-with-helper when available; agent fallback when not,
  with escalation-to-human for architectural decisions above a declared
  threshold.

Teams declare their preferred pattern in project config. The harness
supports all patterns but flags when high-stakes decisions are being
made by agents alone, so the tradeoff is visible.

## Ownership map

Ownership is declared at the project level alongside workflow and role
configuration:

```yaml
ownership:
  spec:
    behaviors: po
    acceptance_criteria: po
    design: sa
    technical_risks: sa
  architecture: sa
  decisions_product: po
  decisions_technical: sa
  coding_conventions: sa
  domain_glossary: po
  security_policy: sa      # or "security" if team has that role
  roadmap: po
```

Artifacts not in the ownership map are either:

- **Orphaned** — the harness refuses modifications until an owner is
  declared. Forces the team to decide.
- **Inherited** — section-level artifacts inherit from parent artifact
  if section isn't declared. Spec inherits from its declared owners by
  default.

## Transferring ownership

Ownership is a role assignment, and assignments can change. If the PO
leaves or gets busy, someone else takes over. Transfer:

1. New owner is declared in project config (or via a harness command).
2. In-flight proposals either route to the new owner (if not yet
   accepted) or complete with the old owner's authority.
3. The transfer itself is a recorded event — who transferred from whom,
   when, why.

Audit trail preserves the history. "Who accepted this spec change?"
resolves to the owner at that time.

## Cross-work-unit visibility

POs and SAs benefit from cross-work-unit context. Unlike most roles,
whose domain is a single work unit, owner roles' domain is the product
or architecture as a whole.

The TUI/web view provides owner dashboards: open proposals targeting
them, recent decisions they've made, work units in phases they
influence. The service supports cross-work-unit queries scoped to the
owner's purview.

This also feeds back into the context bundle model: an owner's context
bundle at spawn includes cross-work-unit state (recent decisions,
current in-flight proposals, product roadmap, architecture document) in
addition to the current work unit's state.

## Decisions as the owner's output trail

An owner's accumulated decisions — both accepted and rejected-with-
reasoning proposals — form an authoritative record:

- **PO** → spec behaviors + acceptance history + product decision
  records.
- **SA** → architecture document + technical decision records + coding
  conventions.

Over time, these become the primary artifacts new team members (human
or agent) read to understand what was decided and why. This is the
feed for problem 6 (agents lack big-picture context) — the owner's
decision trail *is* the big picture.

Rejected proposals with reasoning are often as valuable as accepted
ones. "We considered X, rejected for reasons Y" prevents re-
litigation. The harness retains rejected proposals in the decision
record alongside accepted ones.

## Scaling down

On a one-person project, the dev fills all roles: dev, reviewer, PO,
SA. The harness supports this; ownership is still declared (same human
is PO and SA); self-approval is tracked with justification.

Proposals in this setup are mostly ceremonial — the dev proposes to
themselves, accepts themselves, applies the change. But the ceremony
produces an audit trail, and that audit trail is still valuable:

- Future team members (human or agent) see what was decided and why.
- The dev themselves benefit from forcing the articulation of
  decisions before making them.
- Transitioning from solo to team is smooth — roles are already
  separated conceptually, just need different humans to fill them.

The harness shouldn't force role separation on single-person projects
but should preserve the structure for when it matters.

## What this does for the original problems

- **Problem 4** (no consistent memory): owner decision trails become
  durable artifacts that outlive individual work units.
- **Problem 6** (no big-picture context): owners are the big picture,
  and their decision trails are what new actors read to absorb it.
- **Problem 7** (agents self-certify): artifact changes go through
  owners, not through the proposer. Same structural discipline as
  phase completion going through evaluators.

## Deliberately deferred

- **Ownership permission model beyond accept/reject.** Read
  permissions, partial-edit permissions, etc. If it's needed,
  probably a v2 concern.
- **Decision record schema.** Decision records themselves have
  structure worth declaring. Deferred until we see what owners
  actually produce in practice.
- **Cross-project ownership.** Same PO/SA across multiple projects.
  Out of scope — one service per repo means one ownership map per
  project.
