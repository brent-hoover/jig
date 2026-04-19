# 02 — Project Spec

The top-of-tree artifact describing what the product is. Owned by the PO.
The root that individual tickets derive from and contribute back to.

## Premise

Tickets don't materialize from nowhere. They implement capabilities
that belong to a product. That product has shape — what it is, who it's
for, what it does, what it deliberately doesn't do. Good teams have this
shape in their heads or in scattered documents. The harness makes it a
first-class artifact so everything downstream can reference it.

The project spec is where the product shape lives. It's:

- The authoritative description of the product.
- The source of capabilities that become tickets.
- The roadmap (future capabilities at varying levels of detail).
- The commitment record (what's been built, what's been explicitly
  ruled out).
- The onboarding document (new humans or agents read it to understand
  what this project even is).

Without it, every ticket reinvents context. With it, tickets
inherit product-level intent and contribute to a coherent whole.

## Progressive elaboration

The project spec doesn't need full detail everywhere. Capabilities live
at different elaboration levels depending on their lifecycle position:

- **Idea** — one-liner or less. Intent captured; details aren't. Useful
  for long-range roadmap items not yet committed.
- **Shaping** — being elaborated. Usually PO work (with helper agent).
  May have in-flight Proposals.
- **Ready** — enough detail to create a ticket from.
- **In progress** — ticket(s) exist and are running. Capability
  references them.
- **Built** — work complete, shipped. Capability reflects what
  actually exists.
- **Archived** — built but no longer relevant. Superseded, removed, etc.
- **Non-goal** — explicit declaration that this will not be built, with
  rationale.

A roadmap naturally contains many ideas at low detail. Only near-term
capabilities need full elaboration. Fleshing out is itself work — often
PO work with helper-agent assistance — that happens before a ticket
is created.

State transitions are events the harness notices:

- Idea → Shaping: usually implicit (someone adds detail).
- Shaping → Ready: explicit (PO declares ready for ticket creation).
- Ready → In Progress: explicit (ticket created, linked to capability).
- In Progress → Built: explicit (ticket closes successfully).

## Human format vs structured format

The project spec exists in two representations:

**Human authoring format.** Markdown with light structure. What a PO
actually writes. Section headers for state categories; capability
headers; prose within. Expresses intent; easy to read and edit; doesn't
require running the harness to work with.

**Structured format.** YAML with stable IDs, ownership annotations,
explicit state enums, cross-references, metadata. What the harness
consumes. Machine-checkable; derivable from the human format; supports
validation and reference resolution.

Both live in the repo, next to each other, git-versioned:

```
.jig/spec/
  project.md              # human format, source of intent
  project.structured.yaml # structured format, source of interpretation
```

The human format is the source of truth for **intent**. The structured
format is the source of truth for **interpretation**. They stay in sync
via the spec agent (below).

## Human format example

```markdown
# todoapp

Task management for individuals. Focus on simplicity — no teams, no
sharing.

## Built

- **Create and manage todos** — basic CRUD, completion states
- **Viewing and organizing** — list view, filtering, basic sorting
- **Accounts and auth** — standard email/password, password reset

## Planned (committed)

### Due dates
Users should be able to give todos due dates and see when they're
overdue. Sort by due date should be an option. Keep it simple — no
recurring dates, no reminders.

### Priorities
Three levels: high, medium, low. Default to medium. Visual distinction
in list view.

## Planned (not yet committed)

- Labels — flexible tagging
- Notes — longer text per todo

## Backlog

- Mobile app
- Keyboard shortcuts
- Export/import

## Non-goals

- Sharing or collaboration
- Calendar integration
- Time tracking
```

Schema enforcement on the human format is intentionally thin — state
section headers, capability headers (level 3 for elaborated, bullets for
one-liners), prose within. Within that structure, the PO writes naturally.

## The spec agent

A specialist role, distinct from dev/reviewer/QA. Its job is
translation, formalization, and consistency — not coding, not reviewing
code.

Responsibilities:

**Parse and structure.** Turn the Markdown sections into a structured
capability tree. Assign stable IDs to capabilities so references survive
reorganization.

**Detect changes.** Compare against the prior structured version.
Identify additions, removals, state transitions, content changes, and
reorganizations.

**Preserve stable data.** Things the human format doesn't express
(capability IDs, completion timestamps, links to completed tickets)
must survive regeneration. The structured version carries metadata the
human format doesn't.

**Flag ambiguities.** If the human text is ambiguous ("flexible tagging"
— shared labels? per-todo? hierarchical?), the spec agent flags it as a
Proposal to clarify rather than guessing. Push back on incompleteness is
a feature, not friction.

**Ensure consistency.** Mechanical checks:
- Non-goals can't contradict planned items.
- Built items can't appear in backlog.
- Cross-references resolve.
- Capability IDs are unique and stable.

**Propose elaborations when needed.** When a capability transitions from
Idea toward Ready (because a ticket is being requested), the spec
agent can propose an expansion draft based on what exists, for the PO to
review and refine.

Tools: read/write on project spec artifacts; read-only on related
context (decision records, past tickets). No code access, no SCM
integration. Its scope is the spec itself.

## Direct edits to structured format

Allowed. Sometimes a correction is easier to make directly — fix an ID,
adjust metadata, patch a cross-reference. The spec agent detects when
the human format no longer regenerates to match the structured version
and flags the divergence for reconciliation.

Reconciliation is usually: update the human format to match, or roll
back the structured edit. The spec agent proposes one or the other;
PO decides.

Forbidding direct structured edits would force the PO through the human
format for every correction, which is heavier than necessary. Allowing
them with divergence detection preserves flexibility without losing
coherence.

## Relationship to ticket specs

Ticket specs and the project spec operate at different scales:

- **Project spec**: what the product is, what each capability is at a
  product level.
- **Ticket spec**: how this specific capability gets built, with
  implementation-relevant detail.

When a ticket is created, it's linked to a capability in the project
spec. The ticket's context bundle includes the capability. Its spec
phase expands the capability into behaviors, acceptance criteria, design
— the level needed to build it. The ticket spec references the
capability it derives from.

When the ticket closes successfully, the capability transitions to
Built. The project spec updates to reflect what actually shipped. The
update is often automated (spec agent drafts it, PO reviews), but the
PO retains authority — they can modify the final description, add
notes about what was learned, etc.

## Proposals against the project spec

Changes to the project spec go through the Proposal mechanism (see
[04](./04-ownership.md)), with the PO as owner. Sources of proposals:

- **PO themselves** — deliberate product direction changes.
- **Devs during work** — "I noticed we could do X; should it be a
  capability?" Proposal to add X.
- **Spec agent** — "this capability has been Idea-state for six months;
  should it move to backlog or archived?"
- **Users (via PO)** — feedback captured and proposed.
- **Ticket discoveries** — implementation surfaces something that
  changes product shape.

The last one is worth attention. Sometimes a ticket discovers that
a capability as described can't work, or that doing it properly
requires a different product commitment. In those cases, the ticket
halts and escalates. A Proposal to modify the project spec is created.
The PO accepts or rejects. If the change is accepted, the ticket
restarts (or is redesigned) against the new project spec. If rejected,
the ticket closes as abandoned or returns to fit the original spec.

Project-spec changes are weighty because they're commitments. The
"halt and escalate" default is correct — project-spec evolution
shouldn't happen as a side effect of implementation work.

## Bootstrap

On day one of a project, there's no spec. Writing the initial version
is a manual setup step the PO does (possibly with helper agent
assistance, possibly just by writing Markdown):

1. PO writes an initial `project.md` — rough first cut, mostly
   one-liners, major non-goals declared, whatever's already built
   captured.
2. PO commits it.
3. The harness detects the new spec and runs the spec agent for the
   first time, producing `project.structured.yaml`.
4. The harness is now fully operational for this project. Future
   changes flow through Proposals.

No automated bootstrap. This is deliberate — the initial project spec
is an act of product definition, done by a human. Automating it would
produce generic output that nobody actually believes in.

## Capability addressing

The structured format assigns stable IDs. Context bundles and
references address specific capabilities:

```
project://spec/capabilities/due-dates
project://spec/capabilities/due-dates#sort-behavior
project://spec/non-goals
project://spec/state/planned
```

Ticket specs reference the capability they derive from:

```yaml
# ticket spec
feature: todo-due-dates
derived_from: project://spec/capabilities/due-dates
```

Decisions reference capabilities when relevant:

```
DR-42: Client-side overdue detection — scope: due-dates capability.
```

This gives every part of the system a clean handle on the
product-level concept.

## What this does for the original problems

- **Problem 3** (agents arrive undereducated): project spec is part
  of every agent's context. Product intent is always in scope.
- **Problem 5** (hard to scale across work sizes): capabilities in
  the project spec pre-structure the work. A large product area with
  many capabilities naturally decomposes into multiple tickets.
- **Problem 6** (no big-picture context): the project spec *is* the
  big picture. Agents and humans read it to understand what the
  product is and where it's going.

## Deliberately deferred

- **Multiple product specs per repo.** Monorepos with multiple
  products would want this. V1 is one project spec per repo, matching
  the one-service-per-repo decision from [12 — Service shape](./12-service-shape.md).
- **Roadmap UI beyond the spec itself.** Visualization, priority
  reordering, timeline estimation. These are nice; the spec as
  Markdown + YAML is sufficient for v1.
- **Spec-driven ticket auto-creation.** When a capability transitions
  to Ready, the harness could offer to create a ticket. Useful but
  optional; teams can do it manually in v1.
- **Cross-project capability sharing.** A capability used across
  multiple projects. Out of scope — one project spec per repo.
- **Historical capability queries.** "When did this capability become
  Built?" is answerable from git history for v1. Dedicated query
  tooling deferred.
