# 03 — Specs, Work Types, and Classification

Specs as first-class, owned, living artifacts, and the classification
model that keys them. This is the mechanism that makes asymmetric
validation possible and makes work-unit decomposition tractable — one
of the features that distinguishes this harness from "agents working
alone on tickets."

## Relationship to the project spec

Work unit specs don't exist in isolation. They derive from capabilities
in the project spec (see [02](./02-project-spec.md)), which is the
product-level description of what's being built.

The project spec and work unit spec operate at different scales:

- **Project spec**: what the product is, what each capability is at
  product level. Maintained by the PO in a human authoring format,
  with the spec agent keeping a structured version in sync.
- **Work unit spec**: how this specific capability gets built, with
  implementation-relevant detail.

When a work unit is created, it's linked to a capability in the project
spec. The work unit's context bundle includes the capability as
context. Its spec phase *expands* the capability into work-unit-level
detail (behaviors, acceptance criteria, design) rather than inventing
from scratch.

When the work unit closes, the capability transitions to "built" in
the project spec. The project spec updates (usually drafted by the
spec agent, reviewed by the PO) to reflect what actually shipped.

## Why structured specs at all

Two things a structured spec provides that prose can't:

**Validation contract.** An asymmetric validation agent (one that
doesn't see the implementation) can derive tests from a structured spec.
A prose spec leaves interpretation gaps the validator has to fill by
looking at code, which defeats the asymmetry.

**Machine-checkable completeness.** "Does this spec have acceptance
criteria for every declared behavior?" is a question the harness can
answer when the spec is structured. On prose it's a judgment call that
often goes unasked.

Beyond those two: structured specs diff meaningfully (behavior B3 added,
edge case E2 removed), decompose naturally (each behavior is a
candidate child work unit), and reference cleanly from other artifacts
(decision records reference specific behaviors by ID).

## Honest history

Variants of this have been tried many times with mixed results. Failure
patterns worth naming:

**Specs as bureaucracy.** Structured spec becomes paperwork teams work
around rather than with. Real thinking stays in conversation; the spec
is performative. Symptom: specs written after the code, to satisfy the
system.

**Schema rigidity.** A schema designed for "features" doesn't fit
refactors, spikes, migrations, investigations. Teams either stretch the
schema grotesquely or abandon it.

**Premature precision.** Requiring button-level detail before
implementation forces decisions that should be made by someone closer to
the work. Decisions get locked in before enough is known. Spec and
implementation diverge.

**BDD/Gherkin as false contracts.** Given/When/Then works when behaviors
are discrete and testable. It warps for continuous, visual,
performance-related, or inherently fuzzy behaviors. Produces ceremony
without clarity.

**Top-down handoff mode.** Spec-author → implementer → validator as a
one-way pipeline. Spec becomes a wall between roles; discoveries during
implementation don't update it; what ships doesn't match the spec.

The harness's approach addresses each of these. Stated explicitly so
teams adopting this know what it's buying and what it can't fix alone.

## Living artifacts, not frozen contracts

The spec is not an output of the spec phase consumed unchanged by
downstream phases. It is an artifact that evolves throughout the work
unit's life:

- Begins at a high level when the work is proposed.
- Gets more detailed as thinking progresses.
- Gets even more detailed as implementation surfaces specifics ("this
  button should say 'completed' when X").
- Reaches full detail by the time work is done, not before.

Different phases refine different levels. Early phases commit to
conceptual shape; later phases add specifics as discovered. The spec at
handoff reflects what was actually built, with the authority of the
owner who signed off on each change.

This is how teams that do specs well already work. The harness supports
the practice rather than forcing a waterfall model.

## Ownership

Specs are owned artifacts per [04](./04-ownership.md). Most spec
sections are jointly owned:

- `behaviors`, `acceptance_criteria`, `out_of_scope` → PO.
- `design`, `technical_risks`, `dependencies` → SA.

Changes go through the respective owner via Proposal thread entries.
The proposer cannot self-accept. An implementer discovering "this
button should say 'completed' when X" proposes the refinement to the
PO; the PO accepts, rejects, or refines.

This is what prevents "the implementer made the spec conform to the
implementation rather than the reverse" — proposer and owner are
different actors, the change is visible and reviewable.

## Structured content

A reasonable default schema for a feature spec:

```yaml
feature: user-manifest-creation
work_type: feature
size: m
owner_product: po
owner_technical: sa

summary: |
  Authenticated users can create, edit, and save manifests from the
  dashboard.

behaviors:
  - id: B1
    given: authenticated user on dashboard
    when: user clicks "New Manifest"
    then: manifest editor opens with empty template
  - id: B2
    given: manifest editor with content
    when: user clicks Save
    then:
      - manifest persisted to database
      - confirmation shown
      - manifest appears in list

acceptance_criteria:
  - all behaviors verifiable through the UI
  - persistence survives session end
  - created manifests visible to creator only

edge_cases:
  - concurrent edits in two tabs
  - invalid content on save
  - network failure during save

out_of_scope:
  - sharing with other users
  - collaborative editing
  - version history

design:
  approach: |
    Manifest is a JSON document stored in the manifests table. Editor is
    a React component using the existing form infrastructure. Save uses
    the standard optimistic-update pattern.
  key_choices:
    - store: existing manifests table (do not create new)
    - editor: reuse FormBuilder rather than custom
  alternatives_considered:
    - custom editor rejected for consistency with other features

technical_risks:
  - none identified

dependencies:
  - authentication system (existing)
  - FormBuilder component (existing)
```

The structured fields are the contract; prose fields (`summary`,
`design.approach`) are where narrative thinking lives. Both are part of
the artifact.

Referenceable IDs on behaviors let other artifacts cite specific
behaviors cleanly: "Decision DR-17 supersedes B2's persistence strategy
for this work unit."

## The three classification axes

Every work unit classifies on three axes. Each axis answers a different
question; collapsing any two into one loses signal.

**Work type.** *What kind of work this is.* Selects spec schema (the
shape of the artifact) and, together with size, selects workflow.
Shipped set: feature, bugfix, refactor, spike, perf, migration, docs.
Projects add or replace.

**Size.** *Estimated effort/complexity.* Scales spec field rigor
within the work type's schema (XS may require only `summary`; M requires
behaviors + acceptance criteria + design). Together with work type,
selects workflow. Shipped set: xs, s, m, l, xl.

**Workflow.** *The phase sequence.* Named, versioned artifact
(see [05](./05-workflow-model.md)). Selected from the catalog by the
`(work_type, size)` pair, overridable at creation within that pair's
`available` whitelist.

Keying workflow on size alone (an earlier simpler design) forces every
work type through the same phase shape per size class — exactly the
schema-rigidity failure named above. A bugfix-M shouldn't run
test-before-implement; a docs-M doesn't have an implement phase in the
usual sense; a spike-S wants a time-box and exit-on-question-answered
rather than implement/review/ship. Keying on `(work_type, size)` gives
each combination its own phase shape.

## Work types

Different kinds of work need different spec shapes. Shipped defaults
(tunable per project):

- **feature** — new user-facing capability. Full schema as shown above.
- **bugfix** — fix for a specific defect. Schema emphasizes: symptom,
  root cause, fix approach, regression test.
- **refactor** — internal restructuring with no behavior change. Schema
  emphasizes: current structure, target structure, migration path,
  equivalence argument.
- **spike** — exploratory work with unclear scope. Schema emphasizes:
  question being investigated, methods, time-box, exit criteria. May
  have no `behaviors` section at all.
- **perf** — performance work. Schema emphasizes: baseline measurement,
  target, hypothesis, measurement plan.
- **migration** — structural migration (schema change, library swap).
  Schema emphasizes: current state, target state, rollout plan,
  rollback plan.
- **docs** — documentation-only work. Minimal schema: scope, target
  audience, deliverables.

Projects add or replace work types in their config. A project with
specialized work (infrastructure, data pipelines, ML experiments) can
declare types with appropriate schemas.

## Schemas as first-class project config

Work-type schemas are artifacts in the repo, owned by the SA (or the
team lead), diffable, updatable through the same Proposal mechanism as
other owned artifacts.

Schema definition roughly:

```yaml
work_type: feature
required:
  - summary
  - behaviors
  - acceptance_criteria
  - out_of_scope
  - design.approach
optional:
  - edge_cases
  - technical_risks
  - dependencies
  - design.alternatives_considered
ownership:
  behaviors: po
  acceptance_criteria: po
  out_of_scope: po
  design: sa
  technical_risks: sa
  edge_cases: po
  dependencies: sa
section_locks:
  # Fields lockable after specific phases
  behaviors: locked_after_phase: spec-approval
validation:
  # Machine-checkable schema rules
  - every behavior has an id
  - every behavior id is unique within spec
  - acceptance_criteria is non-empty if behaviors is non-empty
```

`section_locks` is worth highlighting: it's the structural complement
to ownership. Ownership says "who can change this." Locks say "at what
points does this become hard to change." A behavior locked after the
spec-approval phase can still be modified via a Proposal, but the PO's
acceptance criteria for that proposal should be higher — you're
changing a committed spec, not refining an in-progress one.

This lets the schema encode team conventions about when fields
stabilize without being rigid about it.

## Size scales field rigor within the schema

Full schemas are heavy. Not every work unit needs the full machinery.
Size doesn't change *which* schema applies (that's the work type's
job) — size scales *how rigorous* each field must be within the schema.

A bugfix at XS may require only `summary` and `fix_approach`; a bugfix
at L requires the full schema plus extra rigor on `regression_test`.
The project's schema config declares the size mapping:

```yaml
work_types:
  bugfix:
    schema: bugfix
    required_by_size:
      xs: [summary, fix_approach]
      s: [summary, symptom, fix_approach, regression_test]
      m: [summary, symptom, root_cause, fix_approach, regression_test]
      l: [summary, symptom, root_cause, fix_approach, regression_test,
          impact_analysis]
```

Each axis does exactly one job: schema shape is a work_type concern;
field rigor is a size concern; phase sequence is a `(work_type, size)`
concern.

Some workflows have no spec phase at all (XS hotfixes often don't) —
see §Spec-free workflows below.

## Workflow resolution

Work-unit creation takes `(work_type, size)` as input. The service
resolves `(work_type, size) → workflow` via the project's
configuration:

1. Look up `workflows.by_type[<work_type>].default_by_size[<size>]`
   in `.jig/config.yaml`. If present, that's the default workflow.
2. Creator can override to any workflow in
   `workflows.by_type[<work_type>].available` at creation time.
3. If the `(work_type, size)` combination has no mapping (e.g.,
   `docs` at size `xl` isn't shipped), creation fails with an
   explicit error. Projects add the mapping if they want to support
   it.

Failing at creation is deliberate. Silent fall-through to some generic
default would produce work units with phase shapes that don't match
the work.

### Config shape

Per-work-type `available` and `default_by_size` maps in
`.jig/config.yaml` (extending [17](./17-directory-layout.md)):

```yaml
workflows:
  by_type:
    feature:
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
        - security-review
    bugfix:
      default_by_size:
        xs: hotfix
        s: bugfix-small
        m: bugfix-standard
        l: bugfix-large
        # xl: omitted — bugfixes rarely are XL; force explicit config
        #     or decompose into feature-like work
      available:
        - hotfix
        - bugfix-small
        - bugfix-standard
        - bugfix-large
    refactor:
      default_by_size:
        s: refactor-small
        m: refactor-standard
        l: refactor-large
      available:
        - refactor-small
        - refactor-standard
        - refactor-large
    spike:
      default_by_size:
        xs: spike-small
        s: spike-standard
      available:
        - spike-small
        - spike-standard
    docs:
      default_by_size:
        xs: docs-small
        s: docs-standard
      available:
        - docs-small
        - docs-standard
    # perf, migration omitted from defaults — projects define when needed
```

Per-work-type `available` is a whitelist of workflows legal for that
work type. A creator picking "docs" cannot override to "epic" — the
combination isn't in `available[docs]`. This prevents nonsense
pairings at creation without requiring explicit rules-engine logic.

Shipped workflow definitions (under `jig/defaults/workflows/` per
[17](./17-directory-layout.md)) are named by shape, not by
`(type, size)`. One workflow can serve multiple `(type, size)` cells:
`hotfix` is the XS default for both feature and bugfix. This keeps the
catalog small and the mapping explicit.

## Immutability

Both `work_type` and `workflow` are **frozen at creation**. Changing
either mid-flight raises unanswerable questions [05] flags for workflow
swap: what happens to completed phases, thread state, evaluator
assignments, spec artifacts.

**Size is also frozen at the classification level**, but the
size-as-signal mechanism below provides the correction path. When
signal fires, the options are:

- **Confirm as-is** — creator/owner overrides the signal; size stays;
  work continues.
- **Decompose** — halt the current work unit, create a parent work
  unit (with revised size, possibly different workflow), re-home
  existing artifacts as appropriate.
- **Abort and recreate** — close the current work unit as abandoned,
  create a new one with correct classification.

No mid-flight re-classification. The correction mechanisms produce a
new work unit (via decomposition or recreation) rather than mutating
the existing one in place.

Reclassification mid-flight ("this feature is actually a spike") is
**abort-and-recreate**. The abandoned work unit's archive preserves
what was done; the new work unit starts with fresh classification and
inherits context (spec fragment, thread pointer, discovered facts) via
a creation-time import if useful.

## Creation flow

1. Creator selects `work_type` (dropdown / CLI flag).
2. Creator selects `size` (dropdown / CLI flag).
3. Harness looks up default workflow for `(work_type, size)`; offers
   override from `available[work_type]`.
4. Creator confirms; work unit created with the triple frozen.
5. Spec phase (if the workflow has one) runs against the work_type's
   schema with size-scaled required fields.

## Size-as-signal

Size is an estimate, and estimates are wrong. A medium feature that
turns out to be large-in-disguise will produce a spec that groans under
the weight of trying to fit. The harness treats **spec size as a
real-time signal about whether the declared size is right.**

Thresholds (project-configurable, with shipped defaults as starting
points):

- Behaviors count per size class.
- Acceptance criteria count.
- Edge case count.
- Overall artifact length.

Warnings fire at three lifecycle points:

**During drafting.** If the evolving spec exceeds thresholds for the
declared size, the harness surfaces: "This spec is larger than typical
for size M. Consider decomposing or re-declaring as L." The author can
confirm-as-is, re-declare, or halt to decompose.

**On spec-phase completion.** Final spec exceeds size-M bounds? Last
cheap off-ramp before implementation commits are made.

**During implement.** If the implement phase is generating heavy
activity — many spec refinement proposals, many deferrals, many
checkpoint milestones without progress — that's a runtime signal the
work was under-sized. The harness surfaces: "This work unit is showing
XL characteristics at size M. Consider halting to decompose."

Creation-time estimates are fallible. The harness provides multiple
correction opportunities as evidence accumulates. The goal isn't to
enforce estimates retroactively — it's to notice when the current
declaration is causing friction and offer the team the off-ramp while
correction is still cheap.

### Universality across work types

The signal applies across all work types, not just features. A bugfix
showing XL characteristics at size M is a real condition — a "simple"
bug that's actually three tangled defects, or a regression that
uncovers an architectural problem. The same warning surfaces. The
human decides:

- Decompose into child work units (parent workflow takes over).
- Abort and recreate at correct size.
- Override and continue — the work is large but the team judges
  decomposition doesn't help (e.g., investigation needs to stay in one
  head).

Decomposition is available for any work type. Some work types decompose
unnaturally (a single spike rarely makes sense as multiple child spikes
— usually it becomes a spike plus a follow-on feature). The "override
and continue" path exists precisely for those cases. The warning is
advisory, not enforced.

The decomposition target workflow for non-feature types may still be
`epic` or a work-type-specific variant — a team that frequently
decomposes migrations would add a `migration-epic` workflow to their
catalog.

## Threshold calibration

Shipped defaults are starting points. Mature projects calibrate
thresholds from their own data:

- The archive of closed work units provides a baseline distribution.
- Harness can compute P50/P90 values per size class and suggest
  threshold adjustments.
- "Warning threshold for M is set to 8 behaviors; actual M work in
  this project averages 11 — consider raising" is the kind of
  suggestion worth surfacing.

Each project's reality is different. A UI-heavy app has more behaviors
per feature than a backend service. A startup operates with tighter
sizes than an enterprise. Calibrated thresholds respect that.

## Scaling up: decomposition

Scaling down (XS/S lighter rigor) was covered under size-scaled rigor.
Scaling up matters more, because large work units are where the spec
machinery becomes actively harmful if not addressed.

The spec overhead we've designed is real, and it's appropriate for
medium work. It is *not* appropriate for work that should have been
three medium work units. Forcing large work through medium machinery
produces bad outcomes — vague specs that cheat the schema, scope creep
as implementation reveals more work, overhead without the benefits.

The answer is **decomposition**: large and XL work units produce child
work units, each with their own spec at an appropriate size.

Decomposition is driven by size:

- **M**: decomposition optional. A single spec usually fits.
- **L**: decomposition recommended. Spec phase output is a spec *plus* a
  decomposition plan describing how the work could be broken down, even
  if the team elects to keep it as one unit.
- **XL**: decomposition required. Spec phase doesn't produce a behavior
  spec; it produces child work units.

This is the primary pressure release for spec overhead. A team that
encounters heavy specs at M size responds by decomposing into smaller
units; a team that starts with XL work gets decomposed into Ms before
any M-level spec work begins.

## Parent work units

An XL (or L that's decomposed) work unit becomes a **parent**. Its
shape differs from standard work units:

- Its spec phase output is a decomposition plan, not a behavior spec.
- It does not have an implement phase directly.
- It waits on its child work units to complete.
- After children complete, it has an **integration phase** for the glue
  work (cross-cutting concerns, unified UX, system-level coherence).
- It has a **parent-level validation** phase that tests the integrated
  whole against parent-level acceptance criteria.
- It closes when integration completes and validation passes.

The parent's spec is smaller than an equivalent monolithic spec would
have been, because behaviors live in children's specs. The parent's
spec carries:

- Decomposition (list of children with titles, sizes, work types,
  dependencies).
- Integration criteria (how children fit together).
- Parent-level acceptance (what the whole experience must satisfy).
- Rationale for the decomposition choices.

See [05](./05-workflow-model.md) for the parent workflow.

## Child work units

Child work units are otherwise standard — they have specs, phases,
workflows, checkpoints. What's different:

- They reference their parent. The parent's integration criteria are
  part of their context.
- Their completion notifies the parent, contributing to parent-level
  readiness.
- Cross-child dependencies may force ordering — "child B must start
  after child A completes." v1 supports simple waits; complex
  dependency graphs are deferred.

A child work unit can itself be decomposed if it turns out to be L or
XL at its own size-as-signal check. Recursion is bounded in practice
(real projects rarely go deeper than 2–3 levels) but the system
supports it.

## Walkthrough perspective

The default workflow walkthrough (a medium feature spec with ~3
behaviors, an evolving spec, a few proposals, validation catching one
real issue) represents appropriate scale for M-sized work. The
overhead is real but proportionate.

For larger work, the equivalent walkthrough involves decomposition
first: "this is XL, its spec phase produces four children." Each child
then runs its own medium-sized walkthrough. The parent handles
integration after children complete. No single work unit carries the
XL cognitive load.

Teams learning the system usually need to calibrate on this: the
instinct is to write a single large work unit for a big feature. The
harness's pressure (size-as-signal warnings, decomposition requirements
at XL) redirects that instinct toward "big features are parents of
smaller children." This is a real cultural shift for some teams and
worth naming up front.

## Spec lifecycle across phases

The spec phase produces an initial spec — typically the conceptual
shape. Downstream phases refine:

- **Spec phase** (if present): initial structure, behaviors defined at
  conceptual level, acceptance criteria drafted. PO accepts for
  completeness.
- **Design/architecture phase** (if present): SA adds `design` and
  `technical_risks` sections. Behaviors locked after this phase per
  schema.
- **Implement phase**: implementer discovers specifics. Each specific
  is a Proposal (e.g., "B2's 'confirmation shown' should specifically
  be a toast with text X"). PO accepts or rejects refinements.
- **Validation phase**: validator may propose acceptance-criteria
  clarifications discovered during validation ("spec says 'persisted'
  but didn't specify 'durable across session' — proposing to add").
- **Documentation phase**: documentation references the final spec.

At work-unit closure, the spec represents the actually-shipped feature,
with the full history of decisions (accepted and rejected proposals)
preserved.

## Asymmetric validation enabled

With structured specs, asymmetric validation becomes viable (see
[10](./10-verification.md)). The validation agent's context bundle:

```yaml
required:
  - workunit://spec.behaviors
  - workunit://spec.acceptance_criteria
  - workunit://spec.edge_cases
  - workunit://spec.out_of_scope
  - project://testing-standards
excluded:
  - repo://src/**
  - workunit://spec.design
  - workunit://spec.technical_risks
```

The validator sees what the feature should do; not how it was built.
Derives tests from behaviors and acceptance criteria. Verifies through
the external interface (UI, API, CLI). Reports failures as symptom
descriptions without implementation cause analysis.

The `excluded` primitive in context bundles (from [07](./07-context-bundles.md))
is what enforces this. The agent literally cannot see excluded paths,
not just politely-asked-not-to.

## Spec quality as a prerequisite

Asymmetric validation works only when specs are rigorous enough to
validate against. Vague specs mean the validator can't generate
meaningful tests, or generates tests with wrong assumptions.

The harness can enforce some rigor (schema validation, completeness
checks) but can't manufacture thought. Teams adopting asymmetric
validation will feel the spec-quality gap acutely — which is the point.
The pressure surfaces spec problems early rather than at "the code
shipped but didn't do the right thing."

Mitigations:

- **Size-scaled.** Small work units have lighter spec requirements, so
  the overhead doesn't kill velocity on simple changes.
- **Helper agents** (see [04](./04-ownership.md)). A PO with an
  agent helper gets drafting assistance — the helper can propose spec
  structure, the human fills in product judgment.
- **Schema validation as a gate.** The spec phase can't complete until
  the spec meets its schema. Teams can't paper over gaps by calling a
  vague spec "done."

Teams that don't invest in spec quality don't benefit from asymmetric
validation. They can still use the harness — just with symmetric
validation and heavier reliance on human review. The harness supports
both; the team picks the tradeoff.

## Spec-free workflows

Not every workflow has a spec phase. XS hotfixes often don't.
Investigation spikes may have only an informal question. Emergency
patches skip spec entirely.

Workflows without a spec phase don't produce a `workunit://spec`
artifact. Downstream phases that would normally reference it either:

- Reference a different artifact (e.g., an issue description, a bug
  report).
- Operate without the spec reference (validation is more limited;
  review depends more on human judgment).

The harness allows this. Trying to force every work unit through a
structured spec is exactly the bureaucracy pattern that kills
adoption.

## Team variation

Teams staff differently, and the spec system accommodates:

- **Dedicated PO human** filling the PO role: spec proposals route to
  them, they accept or reject. Helper agent drafts if configured.
- **Dev-as-PO**: small teams, same human wears both hats. Self-
  approval tracked in audit. See [04](./04-ownership.md).
- **Agent PO**: no human available. Agent fills role, escalates
  non-routine decisions. Visible in audit that this occurred.
- **No PO, no spec**: certain workflows (hotfix, spike) don't require
  PO involvement.

The harness reports which pattern is in use per work unit, so teams
see the quality gradient their current staffing produces.

## Load-time validation

`jig start` (and `jig validate`) check the config for:

- Every `workflows.by_type.<type>.available` entry exists as a workflow
  definition.
- Every `default_by_size` value is in `available` for the same type.
- Every `work_types.<type>.schema` maps to a real schema definition.
- Every size class referenced in `required_by_size` is a declared
  size.

Consistent with [17](./17-directory-layout.md) §Validation at load —
discoveries happen at startup, not at work-unit creation runtime.

## What this does for the original problems

- **Problem 3** (agents arrive undereducated): spec is part of the
  work-unit context bundle; agents arrive knowing what to build.
- **Problem 5** (hard to scale across work sizes): the `(work_type,
  size)` matrix scales both axes independently. Work types + size
  scaling give different-sized work different spec rigor. A team adds
  a new work type by declaring its schema and a matrix row; sizing is
  then orthogonal.
- **Problem 6** (big-picture context): spec decision history +
  accepted/rejected proposals = authoritative record of what was
  decided and why for each work unit.
- **Problem 7** (agents declare work done): asymmetric validation
  tests against the spec, not against the implementation. Correct
  workflow for the work type means correct evaluators and checks — a
  bugfix workflow's regression-test phase can't be skipped by routing
  through a feature workflow that doesn't have one.

## Deliberately deferred

- **Cross-spec relationships.** Features that depend on other
  features' specs (B-feature's behavior references A-feature's
  behavior). Real need but complex; deferred past v1.
- **Spec versioning beyond git.** Git versioning is sufficient for v1.
  More sophisticated versioning (named versions, explicit diff-
  between-versions UI) can come later.
- **Custom schema DSL.** Shipped schemas are YAML with declared
  required/optional fields. A richer schema language (conditional
  fields, cross-field validation) can come later if the simple
  approach proves insufficient.
- **Spec import/export.** Integration with existing requirements
  systems (Jira epics, linear issues, etc.). Out of scope for v1;
  extensibility point exists via custom URI resolvers.
- **Cross-axis workflows.** Workflows that cut across work types
  (e.g., "security-audit" applicable to any work type). Shipped
  workflows are listed per-work-type in `available`; a project can
  duplicate a workflow reference across multiple types, but there's no
  first-class cross-type workflow concept. Revisit if patterns emerge.
- **Derived work types.** "Feature with security-sensitive
  components" as a sub-type of feature with additional required spec
  fields. Flat work type list for v0.2; hierarchy deferred.
- **Dynamic workflow selection.** Workflow chosen based on runtime
  state (e.g., "if work touches .auth/, use security-review
  workflow"). Static per-creation selection for v0.2.
- **Soft size classes.** Continuous sizing (story points, t-shirt +
  half-sizes, ranges) rather than discrete xs/s/m/l/xl. Discrete
  classes for v0.2; ranges can map to discrete classes for the config.
- **Auto-reclassification on size-as-signal fire.** Currently the
  signal surfaces a warning and the human chooses; the system doesn't
  auto-convert the work unit. If warnings prove reliable, an "auto-
  decompose when signal fires three times" policy could be added.
