# 01 — Core Concepts

The vocabulary the rest of the architecture builds on. These are conceptual
pieces, independent of implementation.

## Work units

An atom of "a thing to be done" with identity, state, history, and the ability
to be referenced by other parts of the system. Work units implement
capabilities from the project spec; they don't materialize from nowhere.
A work unit carries:

- A size class (spike, small, medium, large, epic) that determines which
  workflow it runs through.
- A definition-of-done — explicit, evaluable criteria.
- A thread (structured comms, see below).
- A context bundle reference.
- A workflow it's progressing through.
- A reference to the project-spec capability it derives from. See
  [02](./02-project-spec.md).
- Optionally, a parent/child relationship. L and XL work units can
  decompose into child work units; the parent then waits on children
  and handles integration. See [03](./03-specs-and-work-types.md) and
  [05](./05-workflow-model.md).

## Project spec

The top-of-tree artifact describing the product. Owned by the PO.
Capabilities live at varying elaboration levels (idea → shaping → ready
→ in progress → built) and transition as work happens. Work units
derive from capabilities; completions update capabilities. The project
spec is the product-level memory the team and agents share.

Exists in two representations: a human authoring format (Markdown) for
PO intent, and a structured format (YAML) for machine consumption.
Spec agent keeps them in sync. See [02](./02-project-spec.md).

## Actors

Humans and agents, treated as peers with different capabilities. An actor has
a role, a current assignment, and a trust level that determines what they can
do unsupervised. Humans are **participants**, not users of the system.

## Roles

A bundle of (responsibilities, allowed actions, required outputs, success
criteria). Roles exist independent of who fills them — a human can be a
reviewer on Monday, an agent can be the reviewer on Tuesday. This is what
makes collaboration real rather than "humans queue work for agents."

Named roles so far: planner/spec, test, dev/implementer, reviewer, validate,
document. Security may be a role in some workflows.

## Policy

Rules governing what any actor can do. Best practices live here: required
tests, required reviews, required security checks, branch protections,
prohibited actions. Policy is **mostly gate, occasionally guide**, and the
guide cases require recorded justification (which itself becomes a decision
record).

One policy definition, enforced at multiple points:
- For agents: via Claude Code hooks at spawn time.
- For humans: via git hooks, CI, review.

## Coordination

How work moves between actors — handoffs, requests for input, blocking and
unblocking, escalation to humans. Distinct from the work itself.

## Observability

The ability for anyone to see current state without interrupting the system.
Who's doing what, what's stuck, what's recent. Read path, separate from the
write path.

## History

An immutable record of what happened — who did what, when, why. Makes the
system debuggable and replayable.

## Verifiability (elevated to core)

The system determines when work is done, not the agent. Completion requires:

- Work unit's definition-of-done criteria are satisfied.
- Objective checks pass (tests, linters, security scans, per policy).
- A different actor (reviewer role, or human) confirms.

An implementing agent can claim "ready for review"; it cannot claim "done."

## Derived concepts (added from problems)

**Threads.** Structured comms attached to work units. Typed entries (question,
answer, objection, resolution, request-for-human). Any actor can post. Visible
in observability. Handoffs and blocks reference specific thread entries. Work
isn't done until thread entries are resolved.

**Context bundles.** Three scopes, loaded explicitly at actor spawn (never
discovered):
- Project-level: architecture, conventions, domain knowledge, decisions.
- Role-level: what each role needs to know generically.
- Work-unit-level: what's happened on this specific task.

**Decision records.** First-class artifacts capturing "why we picked X over Y."
Referenced by work units. Survive beyond the work unit that produced them.
Dogfooded in this conversation.

**Definition-of-done.** Per work unit, explicit, evaluable criteria — both
human-acceptance rubric and automated checks.

**Verification.** The step between "claimed ready" and "marked done," involving
objective checks plus reviewer sign-off.

**Workflow templates.** Keyed by work-unit size/type. Define the role chain,
handoffs, and gates. See [05 — Workflow model](./05-workflow-model.md).

## Tensions named and resolved

- Centralized orchestrator (not distributed). See [12](./12-service-shape.md).
- Push work assignment (agents don't sit idle). See [12](./12-service-shape.md).
- Mostly synchronous chains with per-dev concurrency limits.
- Trust model: agents work autonomously within sandboxed scope; escalate when
  stuck. Sandbox via Docker + bubblewrap.
- Policy as gate (default), occasionally guide with justification.
- State location: service-owned. See [12](./12-service-shape.md) §State location.
