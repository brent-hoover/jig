# Bite-Sized Work, Coherent Whole: Integration Techniques

This note captures additional techniques for keeping independently implemented
agent tickets compatible at runtime. Jig already has typed product-development
artifacts, contracts, and tracer bullets. The purpose here is to add redundant
integration pressure around those mechanisms so locally-correct work continues
to compose into a working app.

## Problem

One common failure mode in agentic coding is:

- each individual ticket is correct in isolation
- each agent satisfied its local acceptance criteria
- contracts exist, but only describe part of the boundary
- when the app runs, the pieces do not fit together

Jig's existing answer is "bite-sized work, coherent whole": keep work small,
but make the integration path explicit through architecture, contracts, and
tracer bullets. The techniques below strengthen that approach.

## Techniques

### 1. Integration Map Per Ticket

Every implementation ticket should declare what it touches and what it expects
from neighboring pieces.

Example:

```yaml
integration_map:
  touches:
    modules:
      - checkout-api
      - order-worker
    routes:
      - POST /checkout
    events_emitted:
      - order.created
    events_consumed:
      - cart.validated
    shared_state:
      - orders
      - inventory_reservations
  depends_on_runtime:
    - inventory.reserve returns reservation_id
    - payment.authorize accepts amount_cents
```

This gives the orchestrator, reviewers, and future agents a graph of join
points rather than only isolated acceptance criteria.

### 2. Tracer Bullets / End-to-End Slices

Jig already has this concept. Bones tickets are tracer bullets: one happy-path
slice through every module an epic touches.

The improvement is not to add tracer bullets again. The improvement is to make
later tickets explicitly preserve, extend, or avoid the tracer path.

Useful additions:

```yaml
tracer_bullet: checkout-happy-path
proves:
  - ui calls POST /checkout
  - api writes order
  - worker receives order.created
  - confirmation page renders order_id
unresolved_edges:
  - payment webhook retry not exercised
  - inventory failure path not exercised
```

Later tickets should state whether they:

- extend the tracer
- depend on the tracer
- change an interface proven by the tracer
- are outside the tracer path

Every ticket that touches a tracer-proven interface should keep the tracer
green.

### 3. Consumer-Driven Contract Tests

When one ticket defines a contract, every consumer should produce at least one
test against it.

Examples:

- API owner publishes response schema.
- Frontend ticket includes a mocked consumer test using that schema.
- Backend ticket includes a provider test satisfying that schema.
- Worker ticket includes an event consumer test against the emitted event
  schema.

Contracts define shape. Consumer tests prove the shape is usable by the code
that depends on it.

### 4. Compatibility Review Before Merge

Add a reviewer whose only job is whole-app fit:

> Does this ticket still fit the current architecture, build plan, tracer
> bullets, runtime graph, and interface registry?

This reviewer should inspect:

- imports and dependency direction
- routes
- events
- schema references
- env vars
- migrations
- feature flags
- public commands
- background jobs
- generated clients or adapters

This is separate from security, spec compliance, test adequacy, and pattern
conformance. Its scope is composition.

### 5. Integration Checkpoints At Handoff

At phase handoff, require the agent to answer structured integration questions.

Example:

```yaml
integration_checkpoint:
  new_public_surfaces:
    - type: route
      name: POST /checkout
    - type: event
      name: order.created
  changed_assumptions:
    - payment.authorize now returns status=pending
  required_followups:
    - update checkout UI pending state
  unknowns:
    - webhook retry behavior
```

This catches "I changed an interface but did not tell anyone."

### 6. Runbook-Style Smoke Tests Per Capability

Each capability should have a tiny runnable smoke path. It does not need to be
exhaustive. It should prove the major pieces can talk.

Example:

```bash
uv run pytest tests/smoke/test_checkout_flow.py
```

The smoke test should be tied to capability or epic, not only to a single
module. It should exercise the app in the same shape the user or operator
cares about.

### 7. Interface Registry With Per-Ticket Diffs

Maintain a generated registry of app-facing surfaces:

- routes
- commands
- events
- database tables
- migrations
- env vars
- background jobs
- external API calls
- feature flags
- generated clients
- adapters

Each ticket can then produce a registry diff:

```yaml
interface_diff:
  added:
    routes:
      - POST /checkout
    events:
      - order.created
  changed:
    env_vars:
      - PAYMENT_WEBHOOK_SECRET
  removed: {}
```

If a ticket changes a public interface without updating the registry, a
compatibility reviewer should flag it.

### 8. Composition Tickets

Not every ticket should build new functionality. Some tickets should
explicitly wire already-built pieces together.

Examples:

- Wire checkout UI to payment API and order worker.
- Connect imported Shopify fixture replay to product-ingest job.
- Replace fake adapter with real adapter behind the same protocol.
- Connect VD wireframe state to the implemented route.

Agent systems often underproduce glue work because glue looks small. But glue
work is where coherence becomes visible.

### 9. Dependency Direction Checks

Architecture should define allowed dependency directions.

Example:

```yaml
dependency_policy:
  allowed:
    frontend:
      - api-client
    api:
      - domain
      - persistence
    worker:
      - domain
      - persistence
    domain: []
  denied:
    domain:
      - api
      - frontend
    frontend:
      - persistence
```

Static checks can then prevent tickets from adding convenient local imports
that make the whole app tangled.

### 10. Golden Path CI

For each app, define one to three golden paths that must keep working.

Examples:

- new user signs up
- user completes the core workflow
- admin views the resulting record

Every ticket should run the golden paths relevant to touched modules or
interfaces. Golden paths are more useful than broad unit coverage for catching
integration drift.

### 11. Assumption Ledger

Agents should record assumptions in a structured way.

Example:

```yaml
assumption:
  id: checkout-api-sync-order-id
  text: checkout API returns order_id synchronously
  owner: checkout-api
  expires_when: payment architecture finalized
  risk: frontend blocks on this
```

Reviewers and coordinators can then detect unresolved assumptions before final
integration.

### 12. Adapter Stubs First

When two pieces will meet, create the adapter boundary early.

Example:

```python
class PaymentGateway(Protocol):
    def authorize(self, request: AuthorizationRequest) -> AuthorizationResult:
        ...
```

Independent agents can then implement behind or against the same adapter
instead of inventing separate versions of the interface.

### 13. Typed Dependency Graph

A dependency graph would be useful to agents if it stays small, typed, and
task-scoped. The goal is not a giant repo diagram. The goal is to answer:

> What could this ticket break?

The useful graph is broader than static code imports. It should represent the
runtime and workflow graph:

```yaml
nodes:
  - id: module:checkout-api
  - id: module:checkout-ui
  - id: route:POST /checkout
  - id: event:order.created
  - id: table:orders
  - id: env:PAYMENT_WEBHOOK_SECRET
  - id: tracer:checkout-happy-path
  - id: ticket:checkout-ui-pending-state

edges:
  - from: module:checkout-ui
    to: route:POST /checkout
    kind: calls
  - from: route:POST /checkout
    to: table:orders
    kind: writes
  - from: route:POST /checkout
    to: event:order.created
    kind: emits
  - from: tracer:checkout-happy-path
    to: route:POST /checkout
    kind: exercises
```

Useful node types:

- modules
- routes
- commands
- events
- database tables / collections
- env vars
- external APIs
- feature flags
- generated clients
- adapter protocols
- contracts
- tracer bullets
- assumptions
- tickets

Useful edge kinds:

- calls
- imports
- reads
- writes
- emits
- consumes
- exercises
- validates
- owns
- depends_on
- assumes
- replaces

What agents get from the graph:

- **Impact awareness** — "I changed `POST /checkout`; what consumes it?"
- **Better context selection** — spawn context can include only nearby modules,
  contracts, tracers, assumptions, and tickets.
- **Reviewer targeting** — changes to events, env vars, migrations, or public
  routes can select the right specialty reviewers.
- **Integration drift detection** — if a tracer-proven interface changes, rerun
  or block on that tracer.
- **Planning signal** — PM can detect tickets that cross too many boundaries and
  should be senior/SA tier.
- **Assumption invalidation** — if a node changes, assumptions attached to that
  node can reopen or require revalidation.

The graph should be queryable, not just visual:

```text
what depends on node X?
what does ticket Y touch?
which tracer bullets exercise this interface?
which assumptions mention this node?
which contracts have consumers but no provider test?
which public surfaces changed since the last green tracer?
```

Possible artifacts:

```text
.jig/graph/dependency-graph.yaml
.jig/graph/ticket-impact/<ticket_id>.yaml
```

Possible MCP tools:

```text
graph_get_impact(ticket_id)
graph_neighbors(node_id, depth=1)
graph_tracers_for(node_id)
graph_changed_interfaces(ticket_id)
graph_assumptions_for(node_id)
```

Agents should usually receive the local graph neighborhood around their ticket,
not an unbounded full graph dump.

## Highest-Leverage Additions For Jig

1. **Interface registry with per-ticket diffs**  
   Makes public surface changes visible and reviewable.

2. **Typed dependency graph with ticket-impact views**  
   Gives agents, reviewers, and PM a shared map of what a ticket could break.

3. **Tracer bullet manifest + preservation gate**  
   Builds on Jig's existing tracer-bullet design by making later tickets prove
   they did not erode the path.

4. **Consumer-driven contract tests**  
   Turns contracts from static shape declarations into executable compatibility
   checks.

5. **Integration checkpoints at handoff**  
   Forces agents to report new surfaces, changed assumptions, followups, and
   unknowns before the next phase consumes their work.

6. **Compatibility reviewer focused on whole-app fit**  
   Gives the review federation a dedicated composition lens.

## Relationship To Existing Jig Concepts

- **Contracts** define expected boundaries.
- **Tracer bullets** prove at least one path works end-to-end.
- **Integration maps** describe how each ticket touches the runtime graph.
- **Dependency graph** connects modules, interfaces, tracers, assumptions, and
  tickets into a queryable impact model.
- **Interface registry diffs** make public-surface changes auditable.
- **Consumer tests and smoke tests** prove the pieces still connect.
- **Compatibility review** catches whole-app drift that local reviewers miss.

The strategy is redundancy by design: no single artifact has to perfectly
prevent integration drift. The system should make mismatch visible through
multiple independent signals before the operator discovers it by running the
app manually.
