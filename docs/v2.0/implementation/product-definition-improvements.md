# Product Definition Improvements For Agentic Coding

This note captures product-definition techniques that help Jig preserve the
"bite-sized work, coherent whole" property before PM/SA/dev decomposition
begins.

The product definition process should not only produce a feature list. It
should produce a coherent product operating model: who the product is for,
which workflows matter, what outcomes are intended, what is deliberately out of
scope, and which assumptions are risky.

## Problem

Agentic coding often fails when product definition is too feature-shaped:

- agents optimize local feature descriptions instead of user outcomes
- capabilities become implementation components
- scope creep enters as "helpful" adjacent work
- edge cases are discovered randomly by later agents
- PM planning decomposes work before the user workflow is clear
- SA architecture optimizes for unstated assumptions

The techniques below make product definition more explicit, testable, and
decomposable.

## Techniques

### 1. Problem Frame Before Feature Frame

Require PO artifacts to distinguish the problem from the solution shape.

Example:

```yaml
problem:
  current_pain: merchants cannot safely preview catalog import changes
  who_feels_it: catalog managers
  frequency: weekly
  cost_of_not_solving: accidental storefront changes and manual cleanup
  current_workaround: upload to a staging shop and compare manually

solution_shape:
  simplest_solution: preview create/update/delete actions before publish
  explicit_non_goals:
    - scheduled imports
    - bulk editing inside the preview table
```

Agents tend to jump to feature shape too early. A stable problem frame helps
later tickets avoid optimizing the wrong thing.

### 2. User Workflow Map

Before suites and capabilities, define the end-to-end user workflows.

Example:

```yaml
workflow: publish-product-catalog
actors:
  - merchant
  - background-worker
start_state: merchant has CSV
end_state: products visible in storefront
steps:
  - upload CSV
  - validate rows
  - preview changes
  - publish
  - reconcile external API result
```

Capabilities should attach to workflow steps. This keeps product definition
grounded in runtime behavior.

### 3. Outcome / Capability Split

Make "capability" mean user-visible ability, not implementation component.

Bad:

```yaml
capability: product table schema
```

Good:

```yaml
capability: merchant can preview imported product changes before publishing
```

Implementation details belong under architecture, contracts, and build plans.
Product capabilities should describe outcomes.

### 4. Explicit Tradeoff Ledger

Product definition should preserve decisions that prevent later scope creep.

Example:

```yaml
tradeoff:
  decision: no bulk edit in MVP
  because: preview/publish loop is the core risk
  revisitable_when: 3 pilot users request it
```

This helps agents avoid "helpfully" adding adjacent features that were
deliberately deferred.

### 5. Negative Scenarios / Non-Happy Paths

Not all edge cases need implementation immediately, but product definition
should name them.

Example:

```yaml
negative_scenarios:
  - duplicate SKU in import
  - external API rate limit
  - user cancels publish halfway
  - stale preview after catalog changes
```

PM can then decide which scenarios belong in bones, MVP, or final instead of
letting agents discover them randomly.

### 6. Acceptance Criteria By User State

Prefer before/action/after state over checklist-only acceptance criteria.

Example:

```yaml
acceptance:
  before:
    - CSV uploaded but not imported
  action:
    - merchant clicks Preview
  after:
    - invalid rows are grouped with reasons
    - valid rows show proposed create/update/delete action
  observable:
    - no external API write has occurred
```

This is harder for coding agents to misinterpret than prose-only criteria.

### 7. Glossary / Domain Ontology Earlier

Domain language should be locked before tickets fan out.

If "catalog", "listing", "product", and "variant" have different meanings, PO
should establish those definitions before PM and SA decompose the work.

This makes ontology a product-definition primitive, not just a cleanup pass.

### 8. Assumption Classification

Product assumptions should be tagged by risk and validation path.

Example:

```yaml
assumption:
  text: merchants import at most 5k rows at a time
  type: volume
  confidence: medium
  validation: pilot data sample
  affects:
    - import-preview
    - background-job
```

High-risk assumptions should create spikes, tracer requirements, or explicit
review prompts.

### 9. Capability Dependency Graph

Before PM planning, PO can declare user-level capability dependencies.

Example:

```yaml
capability: publish catalog
depends_on:
  - preview catalog changes
  - validate imported rows
  - authenticate merchant
```

This is product-level, not code-level. It helps PM avoid building capabilities
in an incoherent order.

### 10. Definition Of "Done Enough" Per Layer

For each capability, PO can define bones/MVP/final expectations.

Example:

```yaml
bones:
  user_can: import one valid CSV and see products created
  excludes:
    - validation UI
    - partial failure recovery

mvp:
  user_can: preview invalid rows and publish valid rows
  excludes:
    - scheduled imports

final:
  user_can: recover from external API partial failure
```

This gives PM and tracer bullets better raw material.

### 11. Open Question Routing

Product docs should distinguish different forms of uncertainty:

- blocking question
- non-blocking uncertainty
- assumption accepted for now
- deliberate non-goal

Agents otherwise treat all uncertainty similarly, either stopping too often or
charging ahead with unstated assumptions.

### 12. Example-First Product Definition

Require concrete examples for important behaviors.

Example:

```yaml
examples:
  - given: CSV row with new SKU
    when: preview runs
    then: action=create
  - given: CSV row with existing SKU and changed price
    when: preview runs
    then: action=update
```

Examples are often more useful than abstract requirements for downstream
coding agents, reviewers, and simulator scenarios.

## Highest-Leverage Additions For Jig

1. **User workflow map before capabilities**  
   Keeps PO output grounded in actual user journeys and runtime behavior.

2. **Capability dependencies at product level**  
   Gives PM a product-ordering graph before implementation planning begins.

3. **Layered "done enough" per capability**  
   Makes bones/MVP/final scope explicit before tickets are generated.

4. **Tradeoff ledger**  
   Preserves deliberate scope decisions so agents do not re-add deferred work.

5. **Example-first acceptance criteria**  
   Gives implementation agents, reviewers, and simulators concrete behavior to
   test against.

## Relationship To Existing Jig Concepts

- **L0/L1/L2/L3 PO flow** can progressively enrich the problem frame, workflow
  map, capabilities, examples, and ontology.
- **Ontology** should be treated as early product infrastructure.
- **Build plans** should consume capability dependencies and layered
  done-enough definitions.
- **Tracer bullets** should be selected from workflow maps, not only feature
  lists.
- **Reviewer federation** can use tradeoffs, examples, negative scenarios, and
  assumptions to catch scope drift.
- **Simulation scenarios** can be generated from user workflows and
  example-first acceptance criteria.

The goal is to make product definition executable enough that downstream
agents can decompose confidently without losing the product's shape.
