# 05 — Workflow Model (partial)

Status: **partial.** Core shape decided; several details deferred.

## What a workflow is

A named, versioned definition of how a ticket progresses from intake to
done. Structured as a directed graph:

- **Nodes are phases**, each with a role, assignment, inputs, outputs,
  automated checks, human acceptance rubric, evaluator, output channel,
  escalation targets, and resource limits.
- **Edges are transitions**, declaring what happens on success, failure,
  or escalation.
- **Entry and exit points are explicit** — where work enters, where it
  exits successful, where it exits failed or abandoned.

Most workflows will be linear and can be expressed compactly. The model
supports loops, branches, and parallelism when needed — most common use
is looping from review back to implement on failure.

## T-shirt sized defaults

Tickets carry a size class (xs / s / m / l / xl) set by whoever creates
the unit. Size is an estimate, not a measurement, and can be wrong — which
is why override is first-class.

The project declares a default mapping from size to workflow:

```yaml
workflows:
  default_by_size:
    xs: hotfix
    s: small-change
    m: standard
    l: large-feature
    xl: epic
  available:
    - hotfix
    - small-change
    - standard
    - large-feature
    - epic
    - security-review   # not default for any size; opt-in
    - spike             # exploratory; opt-in
```

Ticket creation:

1. Creator sets size.
2. Harness looks up the default workflow for that size.
3. Creator can override to any workflow in `available` at creation time.
4. The ticket records both the declared size and the workflow actually
   used.

Capturing size-vs-workflow-used gives a free audit signal over time:
defaults that are consistently overridden are miscalibrated; never-used
workflows probably shouldn't exist.

## Default workflows per size — rough starting point

Not prescriptive; teams tune their own. Rough shape of what ships:

- **xs (hotfix).** implement → agent-review → create-pr → human-review →
  merge. No spec, no separate test phase — existing tests are the net.
- **s (small change).** implement → test → agent-review → create-pr →
  human-review → merge. Tests added alongside implementation.
- **m (standard).** spec → test → implement → agent-review → create-pr →
  human-review → document → merge.
- **l (large feature).** Adds design-review (human sign-off on approach)
  after spec. May add security-review depending on project config.
- **xl (epic).** Parent workflow. Spec phase output is child work
  units, not a behavior spec. Parent waits on children, then runs
  integration and parent-validation phases before closing. See
  decomposition section below.

Test-before-implement in m/l is a strong-TDD opinion. Teams that disagree
replace the default mapping or the workflow definition.

## Overrides

Three flavors, three different answers:

**Size-appropriate override at creation.** "Small in size but high in
impact — use the standard workflow instead of small-change." Creator
picks from the `available` list. Normal and expected.

**Ad-hoc one-off workflow.** Not supported inline. If a phase sequence is
worth running, it's worth naming and adding to `available`. Keeps the
workflow catalog clean and auditable.

**Mid-flight workflow swap.** Not supported. If scope changes mid-flight,
the options are: finish the current workflow then create a follow-up, or
abort-and-recreate at the new size. Swapping mid-flight raises unanswerable
questions about completed phases, thread state, and evaluator assignments.

## Phase properties

What a phase declaration expresses:

**Role.** The kind of actor needed (dev, reviewer, planner, etc.).

**Assignment.** How the specific actor is resolved:
- `spawn_agent: <template>` — orchestrator spawns a new agent instance.
- `specific_human: <id>` — wait for a particular person.
- `any_human` — any human on the team, first to claim.
- `any_human_with_role: <team_role>` — any human with the specified team
  role.
- `previous_actor` — the actor that did a referenced prior phase. Lets a
  workflow say "the implementer addresses the reviewer's objections."

Agent work is a spawn; human work is a wait-for-claim. The harness
presents pending claimable work to humans through the TUI/web view.

**Inputs and outputs.** Declared artifact types from prior phases and
produced for downstream phases. Making these explicit lets the harness
validate the chain at load time — if spec produces a design doc and test
expects one, that's checkable upfront rather than at runtime.

**Evaluator.** Who decides the phase is done. Almost never the role that
did the work (self-certification is problem 7). Typically a different
role, a specific human, or an automated check set.

**Acceptance.** Split into two fields because they're different things:
- `human_acceptance` — prose rubric the evaluator uses.
- `automated_checks` — list of executable gates the harness runs.

Phase completes when both pass.

**On-failure transition.** Default: loop back to a prior phase (usually
implement). Override to halt, skip, or escalate.

**Output channel.** Where the phase's output artifacts land. Applies to
review and other feedback-producing phases:
- `thread` — entries on the ticket thread.
- `pr_comments` — posted to the SCM as PR review comments.
- `both` — posted to both.

Agent pre-PR review typically uses `thread` (fast iteration, no PR noise).
Human PR review uses `pr_comments` (humans review PRs where PRs live).
Phases with `pr_comments` require a PR to exist — meaning a prior
create-pr phase.

**Escalation targets.** Legal targets for questions and escalations from
this phase, with reasons:

```yaml
escalation_targets:
  - role: planner
    reason: "architectural decisions beyond phase scope"
  - role: reviewer
    reason: "standards/convention clarifications"
  - target: human
    reason: "policy conflicts, resource exhaustion"
```

Reasons aren't decorative — they become part of the agent's spawn context.
The orchestrator enforces these constraints. Agents that don't know the
right target post `thread_uncertain` and the orchestrator routes. See
[08 — Threads](./08-threads.md).

**Optional human approval gate.** `requires_human_approval: true` forces
a human sign-off between phases, independent of the phase's own
evaluator.

**Resource limits.** Max duration, max tokens, max tool calls. Inherit
from role template with override.

## Opinions worth calling out

A few things baked into the shipped defaults that are opinions, not
universals:

**Test-before-implement** is TDD. Defensible default; not universal.

**Agent review before PR creation.** Catches issues in a fast loop before
humans look. Relies on having a reviewer role that produces useful review.

**Document as a terminal gate** for m and above. Forces the issue instead
of "someone will do it later." Needs explicit acceptance criteria to avoid
rubber-stamping.

**PR creation as an explicit phase.** The workflow author decides when
work becomes visible to the SCM. No magic side-effects of earlier phases.

**No merge/deploy phase content yet.** Merge ownership is an open question
(see [99](./99-open-questions.md)). Whether the terminal phase invokes the
SCM API or just observes a human-driven merge depends on the answer.

## Review and validate — defining the distinction

The earlier draft had both. Keeping both needs a clear distinction:

- **review** reads artifacts and gives judgment. Feedback goes on the
  thread or the PR. Catches what humans (and agent-reviewers) catch by
  reading.
- **validate** runs things and confirms behavior. Executes tests, runs
  security scans, checks policy compliance. Feedback is pass/fail with
  evidence; failures produce objections.

If the distinction blurs in practice, collapse to one. v1 can ship with
just review until validate earns its place.

## Decomposition and parent workflows

Scaling up requires a mechanism. Forcing L/XL work through a medium
workflow produces vague specs and scope creep; forcing it through a
heavier workflow produces bureaucracy. The answer is to break it up.
See [03](./03-specs-and-work-types.md) for the full size-as-signal
and decomposition model.

At the workflow layer, decomposition means:

- **L-size default workflow** optionally produces a decomposition plan
  in its spec phase. If the plan is used, L becomes a parent workflow.
- **XL-size default workflow** is a parent workflow — decomposition
  is the spec phase output.

A parent workflow has a different shape from a standard workflow:

```yaml
# parent workflow (xl)
phases:
  - name: decompose
    role: planner
    output: list of child tickets (titles, sizes, types, deps)
    evaluator: po + sa (joint — scope and feasibility)
  - name: children
    # Not a phase in the usual sense — parent waits here until all
    # children complete. Scheduler handles child spawning per the
    # dependency order declared in decomposition.
  - name: integration
    role: dev
    task: glue code, cross-cutting concerns, unified UX
  - name: integration-review
    role: reviewer
  - name: parent-validation
    role: qa
    # Validates the whole feature against parent-level acceptance
    # criteria, not against individual child specs
  - name: close
    # all children closed, integration verified
```

Parent doesn't have an implement phase directly — implementation
happens in children. Integration is the only implementation-shaped
work at the parent level, and it's specifically the glue.

## Parent/child relationship

A ticket can be:

- **Standard** — no parent, no children. The case we've been
  designing for throughout.
- **Parent** — has children, waits on them, has integration and
  parent-validation phases.
- **Child** — has a parent, completion notifies the parent.

Parent/child relationship is fixed at creation. Children can't be
reassigned between parents. Parent can't add new children after the
decompose phase completes, except through a proposal-and-accept flow
(child tickets are owned artifacts of the parent, changes go through
the owner).

Children run as standard tickets with the addition that their
context bundle includes the parent's integration criteria. When a
child completes, the parent's state updates; when all required children
complete, the parent's integration phase becomes eligible to start.

## V1 scope

In v1:

- Basic parent/child relationship as above.
- Simple wait-on-all or declared-dependency ordering of children.
- Two levels of decomposition (parent → child; child can itself be
  parent if it turns out L/XL).

Deferred past v1:

- Complex dependency graphs among children.
- Partial parent completion (closing parent before all children).
- Mid-flight rebalancing (moving work between children).
- Recursion deeper than 2 levels.
