---
title: SA Contracts — Design
type: design
status: draft
owner: brent
created: 2026-04-30
problem: ./problem.md
---

# SA Contracts — Design

## Summary

The SA (System Architect) becomes a continuous, discovery-loop role that produces structured architectural contracts at
two scopes: project-level (`architecture.yaml`) and per-module (`modules/<m>/contracts.yaml`). Contracts cover
integration boundaries — data shapes, API shapes, ownership, lifecycle, cross-cutting policies. The SA also maintains a
**risk register** and proposes bounded **spikes** to de-risk architectural unknowns before they become foundational
mistakes. Contracts get enforced by a code review layer (separate design) and amended via a defined escalation path when
implementation surfaces gaps.

SA discovery is sequential to PO discovery: PO produces the brief / discovery / suites / suite-briefs; operator declares
PO "done for now"; SA fires.

## Sequencing relative to multi-level spec, VD, and PM workflow

```
L0 Pitch         (PO)
L1 Discovery     (PO)  — personas, journeys, capabilities
L2 Suites        (PO)  — capability groupings
L3 Suite briefs  (PO)  — behaviors, behavior AC per suite
   ─────────  operator declares PO "done for now"  ─────────
SA Architecture  (SA)  ─┐  parallel; both consume PO
VD Discovery     (VD)  ─┤  artifacts; neither blocks the
                        │  other (see docs/visual-design/)
   ─────────  operator declares SA + VD "done for now"  ─────────
Spike tickets    (dev) — bounded exploration to mitigate risks
SA delta + VD delta    — fold spike learnings back in
   ─────────  Planner PM fires (see docs/pm-workflow/)  ─────────
Build plan       (PM)  — epics × bones/MVP/final layers,
                         tracer-bullet vs standard tickets,
                         dev tier + reviewer set per ticket;
                         UI tickets reference VD wireframes
L4 Tickets       (dev) — bones-first across all epics, then MVP,
                         then final; reviewed by federated
                         reviewer agents at matched tiers
                         (visual_compliance reviewer for UI tickets)
```

SA and VD run in parallel because they have orthogonal concerns:

- **SA decides *backend* technical shape** — server frameworks, databases, message buses, integration patterns,
  deployment, language for backend services.
- **VD decides *frontend* technical shape AND *visual* shape** — frontend stack (HTMX + Alpine by default), build
  tooling, component implementation pattern, layouts, hierarchy, components, brand.

VD is the architect for the frontend, not just the visual designer. The frontend stack choice is inseparable from visual
implementation patterns (HTMX vs React vs Alpine produce fundamentally different codebases for the same wireframe), so
it belongs to VD rather than SA. SA may have opinions where backend and frontend connect (e.g., does the backend render
server-side HTML or only emit JSON?) but the frontend technology is VD's call.

Neither blocks the other; both have to land before PM can produce a build plan.

PO discovery, SA discovery, VD discovery, and Planner PM never run concurrently *within their own role*. They alternate:
PO completes → SA + VD complete in parallel → spikes (if any) → SA delta + VD delta → Planner PM produces build plan →
operator confirms → L4 tickets run via Coordinator PM. On change at any layer, the corresponding delta pass fires and
cascades downward.

## Levels of resolution within SA

| Level | Artifact | What's there | "Done" means |
|---|---|---|---|
| SA-project | `.jig/arch/architecture.yaml` | Cross-cutting tech decisions (data stores, message bus, auth model), module list, cross-module contracts (shared shapes, events), cross-cutting policies, risk register | Operator confirms; covers everything that crosses module boundaries. |
| SA-module | `.jig/arch/modules/<m>/contracts.yaml` | Module-internal contracts: which collections it owns, which APIs it exposes, integration AC on its capabilities | Operator confirms per module; "done enough" allowed (open questions tracked). |

(SA-suite is not a level — suites are PO concepts. SA reads suites to know which capabilities a module implements.)

## Artifacts on disk

```
.jig/
  arch/
    architecture.yaml                      Project-level: stores, modules, cross-cutting, risks
    modules/
      <module-name>/
        contracts.yaml                     Per-module: ownership, schemas, integration AC
    contracts/
      shared/
        <name>.yaml                        Shared contract shapes (referenced by URI)
```

## architecture.yaml — sketch

```yaml
spec_version: 1
data_stores:
  - id: main-db
    kind: postgres
    rationale: ACID, mature, team familiarity
    accessed_by: [orders-service, catalog-ingest, categorization]
  - id: search-index
    kind: opensearch
    accessed_by: [query-service]

modules:
  - id: catalog-ingest
    title: Catalog Ingest
    summary: pulls from customer systems, normalizes
    implements_capabilities:
      - shopify-connect
      - csv-upload
      - normalize-skus
    owns: [products, ingestion_runs]
    tier_hint: senior            # PM uses this to size dev/reviewer
    requires_tracer_bullet: true # foundational module → bones first
  - id: categorization
    title: SEO Categorization
    summary: proposes SEO-friendly categories from normalized catalog
    implements_capabilities:
      - propose-categories
      - dedupe-categories
    owns: [proposed_categories]
    tier_hint: senior
    requires_tracer_bullet: true

shared_contracts:
  - id: product-shape
    type: data
    description: Normalized product record. Owned by no single module; agreed shape.
    schema_ref: project://arch/contracts/shared/product
  - id: catalog-ingested-event
    type: event
    publisher: catalog-ingest
    subscribers: [categorization, analytics]
    payload_ref: project://arch/contracts/shared/catalog-ingested

cross_cutting_policies:
  - id: pii-encrypted-at-rest
    polarity: positive
    rule: All PII fields must be encrypted at rest. Applies wherever PII is stored.
    auto_generates_integration_ac: true
  - id: secrets-via-env
    polarity: positive
    rule: Secrets MUST come from env vars or a secret manager.
  - id: no-direct-db-cross-module
    polarity: negative
    rule: A module MUST NOT write directly to another module's owned collections. Use the owner's API.

risks:
  - id: r-seorank-latency
    text: Unknown if SEORank API is fast enough for our batch sizes.
    impact: high          # blocks categorization MVP if too slow
    likelihood: medium
    status: spike-proposed
    spike_ticket: spike-seorank-latency
    accepted_if: null
  - id: r-shopify-delta
    text: Unclear whether Shopify's API supports clean delta sync of catalog changes.
    impact: medium
    likelihood: medium
    status: spike-proposed
    spike_ticket: spike-shopify-delta
  - id: r-multi-tenancy
    text: Architecture choice — single-instance multi-tenant vs per-customer instance.
    impact: high
    likelihood: high
    status: open           # not a spike; needs operator decision
    blocking: [api-shape, db-schema]

open_questions:
  - id: q-event-bus-choice
    text: "Pick an event bus: Kafka, NATS, or in-process pubsub?"
    blocking: [catalog-ingested-event, all event-driven contracts]

change_log:
  - revision: 1
    date: 2026-04-30
    summary: initial pass — modules and shared contracts identified; 3 risks logged.
```

## modules/<m>/contracts.yaml — sketch

```yaml
spec_version: 1
module: catalog-ingest

owns:
  - collection: products
    db: main-db
    schema_ref: project://arch/contracts/shared/product
    write_access: [self]
    read_access: [categorization, query-service]
  - collection: ingestion_runs
    db: main-db
    write_access: [self]
    read_access: [self]

external_dependencies:
  - id: shopify-api
    kind: external_http
    rate_limit: 2 req/sec per shop
    auth: oauth2 token per shop, stored encrypted in oauth_tokens
    failure_mode: retry with exponential backoff; fail batch after 3 retries
  - id: customer-csv
    kind: file_upload
    max_size: 100MB
    failure_mode: reject with structured error before parsing if > max

exposes:
  - id: ingest-trigger
    kind: internal_api
    method: POST
    path: /catalog/ingest
    body: { customer_id: str, source: enum["shopify"|"csv"], ... }

emits:
  - id: catalog-ingested-event
    when: end of successful batch
    payload_ref: project://arch/contracts/shared/catalog-ingested

integration_ac:
  - capability: shopify-connect
    must:
      - "OAuth tokens stored in oauth_tokens collection, encrypted at rest"
      - "Token refresh handled before expiry"
      - "Rate limit honored: 2 req/sec per shop"
  - capability: normalize-skus
    must:
      - "Writes normalized records to products collection only"
      - "Emits catalog-ingested-event after each successful batch"
      - "Re-ingesting the same SKU is idempotent"

open_questions:
  - id: q-partial-failure
    text: "If 90% of SKUs ingest successfully and 10% fail, do we commit or rollback?"

change_log:
  - revision: 1
    date: 2026-04-30
```

## Contract types

Contracts come in several shapes; the SA decides which apply at each integration boundary. **Where a standardized format
already exists, the SA references it rather than reinventing** — our `contracts.yaml` is the index, the actual schemas
live in OpenAPI / Protobuf / SQL DDL / etc. where appropriate.

| Type | What it constrains | Common formats | Example |
|---|---|---|---|
| **Shape / data** | Structure of a record, payload, file, message body, db row, config | JSON Schema, Pydantic, Protobuf, Avro, SQL DDL | `product-shape`, event payloads, `oauth_tokens` table |
| **Interface (network API)** | Request/response of a REST / RPC / GraphQL endpoint | OpenAPI, gRPC `.proto`, GraphQL SDL | `POST /catalog/ingest` body + response |
| **Code-level interface** | Function signatures, Protocol/interface types, plugin hooks, public exports | Language-native (Python `Protocol`, TS interface, Go interface, Java interface), SemVer | `ProductNormalizer` Protocol; plugin registration hook |
| **Lifecycle / event** | When events fire, who consumes, ordering, retention | CloudEvents, Kafka schemas, custom YAML | `catalog-ingested-event` after batch completes |
| **Behavioral** | Preconditions, postconditions, invariants, side-effects (Design by Contract) | YAML rules; sometimes language-native (Python `assert`, `pydantic` validators, contract libs) | "Either every product persists AND batch=completed, or none persist AND batch=failed" |
| **Ownership / authority** | Who can write / read a resource; what each module is permitted to do | SA-authored YAML | `products` owned by `catalog-ingest`; `categorization` reads only |
| **External dependency** | Constraints imposed by something outside our control | Reference upstream docs | Shopify rate limit, SEORank SLA, customer CSV format |
| **CLI / process boundary** | Flags, arguments, exit codes, stdout shape | click decorators, man-page style YAML | `jig story <ticket>` exit codes + stdout JSON |
| **Cross-cutting policy** | System-wide rules with machine-checkable predicates | SA-authored YAML with `applies_when` predicate | "All PII encrypted at rest"; "No hardcoded secrets" |

**Behavioral contracts get their own section below** — they're the highest-leverage type for agent dev because they
constrain *what must be true* without prescribing *how*. Most of the rest are shape declarations; behavioral contracts
are rules.

## Contract polarities

Contracts can be expressed as:

- **Positive** — "X writes to Y": clearest when asserting what *should* happen.
- **Negative** — "X does NOT write directly to Z's db": clearest when the violation is a known foot-gun (e.g. "no
  hardcoded secrets", "no direct cross-module db writes").
- **Cross-cutting / universal** — "ALL PII fields encrypted at rest": applies system-wide, auto-generates integration AC
  on every relevant capability.

All three are valid contract shapes. The SA prompt should accept all three forms and the schema should encode them
distinctly so code review can enforce them differently (positive: check presence; negative: check absence;
cross-cutting: check applies wherever predicate matches).

## Concrete contract examples — catalog/SEO walkthrough

What contracts actually look like, one example per type, drawn from the SEO-focused catalog search service used as the
running example in `problem.md`. Treat these as templates, not as an exhaustive enumeration — the point is to make the
shape concrete enough that an SA agent (and an operator reviewing SA output) knows what "good" looks like.

**Data contract** — a shape neither module owns; both must agree:

```yaml
- id: product-shape
  type: data
  description: |
    Normalized product record. Owned by no single module; agreed shape used across catalog-ingest (writer) and
    categorization, search-api (readers).
  schema:
    fields:
      - { name: id, type: string, required: true, doc: "Internal stable id, not customer SKU" }
      - { name: customer_id, type: string, required: true, pii: false }
      - { name: customer_sku, type: string, required: true }
      - { name: title, type: string, required: true }
      - { name: description, type: string, required: false }
      - { name: price_cents, type: integer, required: true }
      - { name: currency, type: string, required: true, doc: "ISO 4217" }
      - { name: categories, type: list[string], required: false }
      - { name: ingested_at, type: timestamp, required: true }
```

**Interface contract** — request/response shape of an API the module exposes:

```yaml
- id: catalog-ingest-trigger
  type: interface
  module: catalog-ingest
  method: POST
  path: /internal/catalog/ingest
  request:
    body: { customer_id: string, source: enum["shopify"|"csv"], source_ref: string }
  response:
    success: { batch_id: string, status: "queued" }
    errors:
      - { code: "INVALID_SOURCE", status: 400 }
      - { code: "RATE_LIMITED", status: 429 }
      - { code: "TENANT_SUSPENDED", status: 403 }
```

**Ownership contract (positive)** — who writes / reads a resource:

```yaml
- id: catalog-ingest-owns-products
  type: ownership
  resource: products
  resource_kind: collection
  db: main-db
  write_access: [catalog-ingest]
  read_access: [categorization, search-api]
  rationale: |
    Single writer prevents race conditions and ensures every product record passes through normalization. Other
    modules read but never mutate.
```

**Ownership contract (negative)** — universal prohibition:

```yaml
- id: no-direct-db-cross-module
  type: ownership
  polarity: negative
  rule: "A module MUST NOT write directly to another module's owned collections."
  enforcement: "Reviewer agent flags any direct db write to a collection not in the module's `owns` list."
```

**Process contract** — invariants about *how* an operation behaves:

```yaml
- id: ingest-idempotency
  type: process
  applies_to:
    capability: normalize-skus
    module: catalog-ingest
  rule: |
    Re-ingesting the same (customer_id, customer_sku) tuple produces the same products record. Specifically: same
    internal id is assigned, same normalized fields, no duplicate rows.
  rationale: "Customer re-uploads must not create duplicates or shift downstream references."
```

**Lifecycle / event contract** — what fires when, who consumes:

```yaml
- id: catalog-ingested-event
  type: event
  publisher: catalog-ingest
  subscribers: [categorization, analytics, search-api]
  emitted_when: "End of successful batch (all SKUs normalized and persisted)."
  payload:
    fields:
      - { name: batch_id, type: string, required: true }
      - { name: customer_id, type: string, required: true }
      - { name: product_count, type: integer, required: true }
      - { name: completed_at, type: timestamp, required: true }
  ordering: per-customer FIFO
  retention: 30 days
```

**Cross-cutting policy** — universal rule with a machine-checkable predicate:

```yaml
- id: pii-encrypted-at-rest
  type: cross_cutting_policy
  polarity: positive
  rule: "All fields tagged `pii: true` must be encrypted at rest using project-wide encryption key."
  applies_to: "Any module persisting fields tagged with pii: true in a shared shape."
  auto_generates_integration_ac: true
  enforcement: |
    Reviewer agent scans diffs touching pii-tagged fields; rejects if storage path doesn't go through the encryption
    helper.
```

**External dependency** — constraint imposed by something outside our control:

```yaml
- id: shopify-api
  type: external_dependency
  module: catalog-ingest
  kind: external_http
  base_url: "https://{shop}.myshopify.com/admin/api"
  rate_limit: "2 req/sec per shop"
  auth: oauth2 token per shop (stored encrypted in oauth_tokens per pii-encrypted-at-rest)
  failure_mode:
    timeout_seconds: 30
    retry_policy: "exponential backoff, 3 attempts, then fail batch"
    on_5xx: "retry"
    on_4xx: "fail batch with error code"
  documentation: "https://shopify.dev/api/admin-rest"
```

**Integration AC** — capability-level constraint that ends up on the suite brief, not in contracts.yaml directly:

```yaml
# Inside modules/catalog-ingest/contracts.yaml, integration_ac section:
- capability: shopify-connect
  must:
    - "OAuth tokens stored in oauth_tokens collection, encrypted at rest per pii-encrypted-at-rest policy"
    - "Token refresh handled before expiry; failures emit auth-token-expired-event"
    - "Rate limit honored: 2 req/sec per shop"
    - "Re-running for the same shop is idempotent (per ingest-idempotency contract)"
```

The integration AC are how cross-cutting policies and shared contracts *show up at the capability level* — the dev agent
working `shopify-connect` sees these as part of the AC, not as a separate set of rules to look up.

## Behavioral contracts (Design by Contract)

Behavioral contracts are the highest-leverage contract type for agent-driven development, and the SA design leans hard
into them. They constrain *what must be true* — preconditions, postconditions, invariants, side-effects — without
prescribing *how* the implementation achieves it. That's exactly the property tenet 1 wants: bite-sized work for the
agent (the implementation choice is theirs), coherent whole (the behavioral contract fences the result-space).

**Why agents need these specifically:**

- A shape contract says "this function takes X and returns Y." It doesn't say what Y *means* in terms of system state.
  An agent can satisfy the shape and still produce nonsense.
- A behavioral contract says "after calling this function, the products collection contains exactly the SKUs that were
  in the batch, no more, no less." That's a postcondition the reviewer can mechanically check, and the dev agent has to
  satisfy regardless of implementation approach.
- Behavioral contracts are the place where the SA captures the *real requirement*, distinct from the surface API.
  Without them, the agent infers requirements from the test suite or worse — from training-data priors.

**The four flavors:**

- **Precondition** — what must be true *before* the operation runs. Caller's responsibility.
- **Postcondition** — what will be true *after* the operation completes successfully. Implementation's responsibility.
- **Invariant** — what stays true throughout the operation / lifetime of the object. Both responsibilities.
- **Side-effect** — what observable state the operation changes (writes, emissions, audit logs). Implementation must
  preserve.

**Example — atomicity invariant on ingest batches:**

```yaml
- id: ingest-batch-atomicity
  type: behavioral
  applies_to:
    capability: normalize-skus
    module: catalog-ingest
  precondition: "batch_id refers to an in-progress row in ingestion_runs (status='in_progress')"
  postcondition: |
    Either: every product in the batch is persisted to `products` AND batch.status='completed'.
    Or:     no products from this batch are persisted AND batch.status='failed' AND batch.error is non-null.
    No partial state is permitted.
  invariant: |
    batch.status transitions are forward-only (queued → in_progress → completed | failed).
    A batch in 'completed' or 'failed' state never changes.
  side_effects:
    - "Writes to `products` collection (per ownership contract `catalog-ingest-owns-products`)"
    - "Updates `ingestion_runs.status`"
    - "Emits `catalog-ingested-event` iff postcondition `completed` branch taken"
  rationale: |
    Partial-batch persistence creates inconsistency between products and ingestion_runs that downstream consumers
    (categorization, analytics) cannot detect or recover from.
```

**Example — multi-tenant isolation invariant (defense in depth):**

```yaml
- id: tenant-isolation
  type: behavioral
  scope: every-query
  invariant: |
    Every query against `products`, `proposed_categories`, or `oauth_tokens` MUST include a `customer_id` filter
    matching the requesting tenant's id. Reads without an explicit tenant context must raise
    `TenantContextRequired` rather than returning rows.
  rationale: |
    Multi-tenant data leak prevention. We have row-level security at the db layer; this query-level invariant is
    defense in depth — both layers must hold.
  enforcement: |
    Reviewer agent flags any query against tenant-scoped collections that lacks a `customer_id` filter or doesn't
    receive a `tenant_context` argument.
```

**Example — idempotent retry semantics:**

```yaml
- id: shopify-fetch-idempotency
  type: behavioral
  applies_to:
    capability: shopify-connect
  precondition: "shop oauth token in oauth_tokens is non-expired (or refresh has already succeeded)"
  postcondition: |
    Calling `fetch_products(shop, since_cursor)` N times with the same arguments produces the same result set
    regardless of N. No side effects on retry beyond the audit log entry per call.
  invariant: "API call counter increments by exactly 1 per invocation, regardless of success/failure outcome."
  rationale: "Network failures must be retryable without producing duplicate ingest records or rate-limit violations."
```

**Example — append-only audit log side-effect:**

```yaml
- id: audit-on-tenant-action
  type: behavioral
  scope: cross_cutting       # applies to every module
  side_effect_required: |
    Every tenant-initiated state change MUST append a row to `audit_log` with
    (tenant_id, action, actor, timestamp, before_hash, after_hash).
  enforcement: |
    Reviewer agent flags any state-changing operation in a tenant-scoped module that doesn't go through the
    `audit_helper.record()` decorator/context-manager.
```

**Why these are mechanically reviewable:**

Each behavioral contract specifies *what condition must hold* in terms the reviewer agent can check by reading the diff.
Postconditions translate directly into test assertions. Invariants translate into static checks (or runtime guards).
Side-effects translate into "did the diff include the required call/emit?" The contract is the test spec — the reviewer
doesn't have to guess what to look for.

**Why behavioral contracts pair with the intent layer:**

A behavioral contract authored through the problem → simplest → complications sequence ends up far stronger than one
written as a flat declaration. The complications (concurrency, failure modes) directly drive the precondition and
postcondition language. "What happens on partial failure?" becomes the postcondition's `Or:` branch.

## Behavior AC vs Integration AC

Two flavors of AC, distinguished by author and scope:

| Layer | Author | Scope | Example |
|---|---|---|---|
| Behavior AC | PO | What the user / capability does | "Natural language inputs 'today', 'tomorrow' are accepted as resolved dates" |
| Integration AC | SA | How the capability integrates with the rest of the system | "Writes to `products` collection; emits `catalog-ingested-event` on completion" |

A behavior may have only behavior AC if it's pure logic. Most behaviors that touch state, external systems, or other
modules will have both. Both contribute to "done"; both are checked in review.

**Boundary discipline:** SA does NOT specify implementation choices (which library, which algorithm, which dedup
heuristic). That's behavior AC or ticket-level decision. SA cares about the shape and the integration; not the
internals. The danger of violating this is contract bloat — "specify everything" defeats the purpose because it stops
being a sharp, code-reviewable constraint and starts being a wishlist.

## Risk identification and spikes

Identifying architectural risk and proposing bounded de-risking work is a first-class part of SA discovery — not a
separate phase.

**A risk is anything that, if wrong, would force significant re-architecture.** Examples:

- "We don't know if SEORank's API is fast enough for our batch sizes" — affects whether categorization can be sync or
  must be async.
- "Unclear if the chosen db can handle 100k product catalogs at our query patterns" — affects fundamental data model.
- "Single-instance multi-tenant vs per-customer instance" — affects everything downstream.

For each risk the SA decides: **spike**, **operator-decide**, or **accept**.

- **Spike**: write a bounded exploration ticket. Output is a *learning*, not production code. Spike tickets get a
  `spike: true` flag and have time-boxed scope. The result becomes a comment on the risk + an SA delta pass that folds
  the learning into contracts (or marks the risk as mitigated / accepted).
- **Operator-decide**: a question of preference or policy that the agent can't resolve. Surfaces as an open question; SA
  pauses on dependent contracts until resolved.
- **Accept**: the risk is real but acceptable; documented for posterity, no spike needed. (E.g., "we accept that Shopify
  may rate-limit us and our SLA reflects that.")

**Spike workflow:**

1. SA logs risk in `risks` section with proposed spike.
2. Operator confirms spike scope and time-box.
3. Spike ticket spawned (`spike: true`, narrow scope, 1-3 hours of agent work).
4. Dev agent runs the spike, produces a comment with findings.
5. SA delta pass reads the spike output, updates contracts / risk status, marks risk `mitigated` or `accepted`.

Spikes are the SA equivalent of "we don't know yet, let's find out before we commit." They are how the architecture
stays honest about its unknowns instead of building 5 modules on a foundational assumption that turns out to be false.

## SA workflow — discovery loop

SA discovery is the same loop pattern as L1 PO discovery, parameterized differently:

```
Walk axis: modules and their integration boundaries
For each module:
  NARRATIVE   read PO artifacts (suite briefs); summarize what
              this module is responsible for
  EXTRACT     propose contracts using the checklist below;
              flag risks where the contract is uncertain
  CONFIRM     show operator the proposed contracts + risks;
              operator edits / drops / adds; confirms or
              modifies spike proposals
  APPEND      write to architecture.yaml + modules/<m>/contracts.yaml
  LOOP        next module
After all modules:
  Pass over cross-module contracts and cross-cutting policies.
  Final risk-register pass: are there project-level risks
  not surfaced per-module?
Gate at end: operator declares "SA pass done for now."
Open questions and open risks carried forward.
```

## The SA checklist (the over-specify lever)

For each module, the SA agent MUST address each of these categories — concrete answer, explicit default, or "N/A:
<why>". Silence on any category is a bug; agents default to terse and will under-specify if not prompted.

- **Data ownership** — which collections / tables / topics does this module own? Who else can read?
- **Access pattern** — sync API call? Async via event? Polling?
- **External dependencies** — third-party APIs, customer systems, file formats. What are their constraints?
- **Auth + credential flow** — how does the module authenticate to others? Where do its secrets live?
- **Failure modes** — timeouts, retries, idempotency, partial failure handling, dead-letter destinations.
- **Cross-cutting policies that apply** — does this module handle PII, run financial transactions, log audit events?
- **Performance budget** — latency, throughput, load expectations (if any).
- **Events emitted / consumed** — what does it publish, what does it subscribe to?
- **Idempotency / ordering guarantees** — re-running OK? Order matters?
- **Risks** — what's uncertain enough that a spike would reduce the risk before committing?

The checklist is the contract between the SA agent and itself. Without it, agents default to terse and miss things; with
it, they over-specify by construction. Tenet 4 in action: the checklist-shaped artifact reads to the agent as a
contract, not a draft.

## What a normal SA pass produces

A medium-sized project's SA pass should produce roughly this shape. Use as a sanity check at confirmation time —
significantly less likely means SA didn't dig deep enough; significantly more likely means contract bloat (the SA is
documenting things that aren't actually integration concerns).

**At the project level (`architecture.yaml`):**

- **1–3 data store decisions** with rationale (postgres for ACID, opensearch for search, s3 for blobs, etc.)
- **A `dev_provisioning` block on every data store** declaring how it gets provisioned for dev agents (strategy defaults
  to `shared_with_namespace`; SA picks `per_agent_ephemeral` only when the service doesn't namespace cleanly; operator
  can override to `operator_supplied`). Required even for trivial projects so the orchestrator knows whether to spin up
  anything at agent spawn — see `docs/dev-environment/`.
- **3–7 cross-cutting policies** — typically pii-encrypted-at-rest, secrets-via-env, no-direct-cross-module-db, plus
  project-specific ones (audit-on-tenant-action, structured-error-envelope, trace-id-propagation, multi-tenant-
  isolation). Note: small projects (CLI tools, single-suite apps) skip most of these.
- **2–5 shared contracts** — cross-module shapes and event envelopes (product-shape, error-envelope, tenant-context,
  catalog-ingested-event)
- **0–3 `external_dependencies`** with `dev_provisioning` blocks — typically `recorded_fixtures` strategy with real
  calls allowed only at tracer-bullet / final layers.
- **4–6 modules** listed with `tier_hint` and `requires_tracer_bullet` flags
- **3–10 risks** in the register (mix of spike-able, operator-decide, and accepted)
- **1–5 open questions** the SA couldn't decide alone

**At each module level (`modules/<m>/contracts.yaml`):**

- **1–3 ownership contracts** — collections / topics / namespaces this module owns, with read access list
- **0–3 external dependency contracts** — third-party APIs, customer-system formats, file uploads
- **1–2 exposed interface contracts** — APIs this module offers (or `spec_ref` to an OpenAPI/proto file)
- **0–2 emitted event contracts** — what this module publishes
- **1–4 behavioral contracts** — invariants, postconditions, side-effects on the module's key operations. *Behavioral
  contracts should be the largest single category if the SA is doing its job — they're where the real requirements
  live.*
- **3–8 integration AC** distributed across the module's capabilities (these end up on the suite brief, layered onto the
  PO-authored behavior AC)
- **0–2 module-specific open questions**

**Quality signals to watch for:**

- A module's `contracts.yaml` with fewer than 5 entries (across all categories): either genuinely simple OR
  under-explored. Operator should sanity-check.
- A module's `behavioral` section is empty: almost always wrong. Every non-trivial module has invariants worth stating.
  If the SA didn't find any, it didn't ask hard enough.
- All risks marked `accepted` with no spikes proposed: the SA is rubber-stamping. Every project has at least 1–2 real
  unknowns worth a bounded spike.
- More than 15 cross-cutting policies at project level: likely over-specification. Cross-cutting policies are expensive
  (they auto-generate AC everywhere); each one needs to earn its keep.
- A data store without a `dev_provisioning` block: the orchestrator can't provision it for agents. Either add the block
  or explicitly mark `dev_provisioning: { strategy: none }` (only valid if no agent will ever need to talk to it during
  dev — almost never the case).

**The catalog/SEO project, fully fleshed out, would look approximately like:**

- Project: 2 data stores (postgres, opensearch), 6 cross-cutting policies, 4 shared contracts, 5 modules
  (catalog-ingest, categorization, search-api, js-snippet, auth-and-tenancy), 8 risks, 3 open questions.
- catalog-ingest module: 3 ownership contracts (products, ingestion_runs, oauth_tokens), 2 external deps (shopify, csv),
  1 exposed API, 1 emitted event, 3 behavioral contracts (atomicity, idempotency, fetch-retry), 7 integration AC across
  capabilities.
- Roughly equivalent shape per other module, scaled to its responsibilities.

That's a real medium-project SA output: sized to be reviewable by the operator in a sitting, dense enough to
mechanically enforce, sparse enough to not become a wishlist.

## Iteration model

SA discovery isn't ever globally complete; it completes-for-now at each gate. Two iteration paths:

**Push (downward) — operator changes upstream:**
1. New journey added at L1 (or new capability at L3).
2. Cascades through L2 / L3 with operator confirmation.
3. Triggers SA delta pass for affected modules.
4. SA proposes contract additions / changes; flags new risks if the change introduces uncertainty.
5. Operator confirms.

**Pull (upward) — dev agent surfaces a gap:**
1. Dev agent on a ticket discovers an unspecified contract ("how should this respond when X happens?").
2. Posts a structured "contract gap" comment on the ticket.
3. SA agent fires for that gap, proposes a contract or amends an existing one, surfaces an open_question if undecidable,
   logs a risk if it reveals architectural uncertainty.
4. Operator confirms; dev agent resumes.

Both paths produce contract revisions, tracked in change_log.

## Contract consumption by dev agents

Contracts are useful only if the dev agent actually has them in context when implementing. Three-tier injection model
(orthogonal to the three-tier dev/reviewer tiering — see PM workflow):

| Tier | What | Why |
|---|---|---|
| **Always-injected** | Cross-cutting policies (PII, secrets, no-direct-cross-module-db, etc.) | Universal, cheap, high cost-of-miss. Every dev agent needs to know these without thinking to ask. |
| **Auto-injected when ticket touches** | Module's `contracts.yaml` + shared contracts referenced by the capability's integration AC | The integration AC are the natural relevance signal — SA decided what mattered for this capability at AC-write time. |
| **Pull on demand** | Anything else — other modules' contracts, change_log, risk register, spike outputs | Dev agent uses MCP tools (`get_contract(uri)`, `list_contracts(filter)`) to fetch when it realizes mid-work it needs more. |

The integration AC mechanism is load-bearing: it's how SA's decisions about relevance propagate to dev agent context
without manual configuration on every ticket. Cross-cutting policies escape this because they apply universally.

Tier of injection scales with dev tier (see PM workflow): standard-tier dev agents get less context than SA-tier dev
agents even from the auto-injected layer.

## Code review enforcement

Code review against contracts is the second half of the coherence story (the first half is contract authoring, this doc;
the third is implementation, PM workflow).

The detailed design — federated reviewer agents per concern, severity tiers (critical/important/notable), structured
comments with URI citations, auto-apply for mechanical fixes, bounded fix loops, reviewer-self-check — lives in
`docs/pm-workflow/design.md`.

What this design needs to commit to from the SA side:

- **Mechanical reviewers cite contract URIs.** Every `contract-violation` comment from a reviewer references the exact
  contract URI it violates. That requires the contracts themselves to be addressable at meaningful granularity — e.g.,
  `project://arch/modules/catalog-ingest/contracts#owns/products/write_access`, not just module-level. The URI scheme
  extension needs to support sub-contract anchoring.
- **Cross-cutting policies have machine-checkable predicates.** "All PII fields encrypted at rest" requires a way to
  mark fields as PII in shared shapes (e.g., `pii: true` on field defs). Without that, the reviewer can't enforce. Same
  logic for "no hardcoded secrets" (detection rules) and similar.
- **Contract amendment cascades.** When a contract is amended, any module implemented against the prior revision is
  potentially stale. The reviewer agent should be able to query "which already-merged work depends on contract X
  revision N?" — implies contracts and ticket implementations both record the revision they were authored against.

## Risks (of this design itself)

- **SA blocks all of L4.** If SA is sequential and a discovery pass is slow, no tickets can run. Mitigation: SA gate is
  "done for now," not "done globally" — partial coverage is OK; module marked `pending` skips into L4 with conservative
  defaults (and an operator warning).
- **Contracts grow stale.** As implementation reveals reality, contracts drift from what's actually built. Mitigation:
  contract revisions are first-class; reviewer agent flags drift; SA delta pass amends.
- **Over-specification creates friction.** Too many contracts, too detailed, and the operator drowns in confirmations.
  Mitigation: SA agent uses scale judgment (todo app skips most of it); checklist defaults aggressively to "N/A" for
  irrelevant categories.
- **Boundary ambiguity with PO.** The line between integration AC (SA) and behavior AC (PO) gets fuzzy in practice.
  Mitigation: document the rule of thumb ("does it constrain the shape / ownership / lifecycle, or does it constrain the
  algorithm / UX / output content?"). Examples in role prompts.
- **Reviewer agent false-flags.** Mechanical contract enforcement may reject diffs that are correct-but-different.
  Mitigation: operator override; reviewer prompt instructs to err on the side of flagging questions, not blocking.
- **Spike scope creep.** A "1-hour spike" turns into a 5-hour partial implementation. Mitigation: spike tickets have a
  hard time-box and are explicitly "throwaway code, output is a learning"; SA reviews the spike output and discards the
  code.
- **Risks logged but never spiked.** Operator marks every risk as `accepted` to make them go away. Mitigation: risk
  status changes are surfaced in the TUI; reviewer agent flags PRs that depend on `accepted` risks of high impact.

## Out of scope

- Full code review agent design (separate doc).
- Auto-generation of code from contracts (e.g., generating Pydantic models from a `data` contract). Useful, but later.
- Contract migration tooling when a contract is amended and existing modules need to update. Manual for now.
- Cross-organization contracts (e.g., shared contracts across multiple jig projects). Not relevant at our scope.
- Visualization tools (ER diagrams, sequence diagrams from contracts). Useful, downstream.

## Open questions

1. **architecture.yaml schema vs many small contract files.** Should `architecture.yaml` inline shared contracts, or
   point at separate files (`contracts/shared/product.yaml`)? Inlining is simpler; separate files scale better.
   Tentative: separate files with `_ref` URIs from architecture.yaml.

2. **Contract reference / inheritance.** Can a module's contract reference a project-level shape? (e.g. integration AC:
   "Conforms to `project://arch/contracts/shared/product`.") Yes, presumably — but the URI scheme needs extending.

3. **SA-suite intermediate level?** Currently SA has SA-project and SA-module. Does it need an SA-suite intermediate
   (per- suite architectural overview, before drilling into modules)? Probably not — suites are operator-organizational
   and SA's job is to think about modules.

4. **Reviewer agent runs when?** Per-commit? End of ticket? On PR merge? Probably end-of-ticket (before resolve), but
   has implications for dev agent feedback loops.

5. **Cross-cutting policy enforcement scope.** "All PII encrypted at rest" — does the reviewer scan every diff for PII
   handling, or only flag when fields tagged as PII appear? If the latter, we need a way to mark fields as PII in the
   shared contracts.

6. **Operator UX for SA confirmations.** SA produces lots of detail (per-module checklists × N modules). Operator needs
   a way to review without drowning. TUI design: collapse "default" / "N/A" answers; surface only the meaningful ones.

7. **Spike ticket lifecycle.** Are spike tickets in the same `tickets.jsonl` as regular tickets, or in a separate
   stream? Separate type-tag (`spike: true`) probably enough; same store. But SA needs a way to query "which spikes are
   in flight" without a full scan.

8. **What happens when a spike says "this is impossible"?** The risk doesn't get mitigated; it gets confirmed. The
   contracts that depended on it have to change. Cascade handling needs to be defined.

9. **SA at L0 — how minimal can it be?** For a todo app the answer might be: SA writes "data_store: sqlite, single
   module" and exits. Need a clear floor that doesn't cost the operator a long confirmation cycle for a trivial app.

## Implementation phases

Not yet planned in detail. Rough order:

1. **Schema** — `architecture.yaml` and `contracts.yaml` Pydantic models. URI scheme extension for `project://arch/...`
   and `project://arch/contracts/shared/...`.
2. **SA agent** — role config, MCP tools (mostly mirrors L1 PO tools — `arch_add_module`, `arch_add_contract`,
   `arch_log_risk`, `arch_finalize`), checklist-driven prompt.
3. **Sequencing** — gate after PO done; trigger SA fire from the daemon-side workflow.
4. **Spike support** — `spike: true` ticket type; SA `arch_propose_spike` tool that creates a spike ticket and links it
   to a risk.
5. **Reviewer agent** — read contracts + diff + post structured review. (Separate doc, separate phase.)
6. **Iteration paths** — push-cascade from L1/L3 changes; pull-escalation from dev agent gap reports.
7. **TUI affordances** — `/sa review`, `/sa risks`, `/sa open-questions`, visualization of contracts on tickets.

## Change log

- 2026-04-30: Initial capture from brainstorming session (brent + claude). Captures: SA as discovery loop, two-level
  artifact shape, contract types and polarities, behavior vs integration AC distinction, sequencing relative to PO,
  iteration model (push/pull), risk register + spike workflow, code review as enforcement layer (sketch). Many open
  questions remain.
- 2026-05-01: Added contract-consumption section (three-tier injection model). Expanded code review section to point at
  PM workflow doc and commit to URI granularity, machine- checkable cross-cutting policies, and amendment cascade
  awareness. Added `tier_hint` and `requires_tracer_bullet` to module schema. Updated sequencing diagram to show Planner
  PM phase.
- 2026-05-01: Made contract types richer and named existing standards per type (OpenAPI, JSON Schema, Protobuf, GraphQL
  SDL, SQL DDL, language-native interfaces). Added Code-level interface and CLI as distinct categories. SA references
  existing formats via `spec_ref` rather than reinventing.
- 2026-05-01: Added "Concrete contract examples — catalog/SEO walkthrough" section with one example per contract type
  (data, interface, ownership both polarities, process, lifecycle, cross-cutting policy, external dependency,
  integration AC).
- 2026-05-01: Added dedicated "Behavioral contracts (Design by Contract)" section. Behavioral contracts are the
  highest-leverage type for agent dev because they fence what-must-be-true without prescribing how. Four flavors
  documented (precondition, postcondition, invariant, side-effect) with multi-example walkthrough including atomicity,
  multi-tenant isolation, idempotent retry, audit-on-action.
- 2026-05-01: Added "What a normal SA pass produces" section with sizing guidelines per project and per module, plus
  quality signals to flag under-/over-specification at confirmation time.
- 2026-05-01: Cross-referenced `docs/dev-environment/`. Added `dev_provisioning` block as a required field on every
  `data_store` (and applicable `external_dependencies`) in the "What a normal SA pass produces" sizing guidance, plus a
  quality signal flagging missing dev_provisioning blocks.
- 2026-05-01: Clarified SA/VD scope split — SA owns *backend* technical shape; VD owns *frontend* technical shape AND
  visual shape. Frontend stack choice belongs to VD. SA may still have opinions where backend and frontend connect
  (e.g., SSR vs JSON), but the frontend technology is VD's call.
