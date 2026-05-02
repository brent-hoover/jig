---
title: PM Workflow — Design
type: design
status: draft
owner: brent
created: 2026-05-01
problem: ./problem.md
---

# PM Workflow — Design

## Summary

The PM role splits into **Planner PM** (strategic, runs in passes) and **Coordinator PM** (tactical, runs continuously).
The Planner PM consumes PO + SA artifacts and produces a **build plan** organized by epics, with each epic decomposed
into three completeness layers (bones / MVP / final). Tickets within the plan are typed (tracer-bullet vs standard),
estimated, assigned a dev tier, and tagged with a reviewer set. The Coordinator PM works the plan, dispatches tickets,
routes escalations, and surfaces stalled work — mostly mechanically, with thin LLM judgment for ambiguous routing.

## Sequencing relative to other workflows

```
PO discovery       (PO)
SA architecture    (SA)
Spike tickets      (dev) — if SA flagged risks
SA delta           (SA)
   ─────────  Planner PM fires  ─────────
Planner PM         (PM)  — build plan with epics + bones/MVP/final
   ─────────  operator confirms plan  ─────────
Coordinator PM     (PM)  — continuous, dispatches tickets in order
Dev + reviewers    per ticket — bones first across all epics, then MVP, then final
```

The Planner PM has the same gate shape as PO and SA: produces an artifact, operator confirms, then continues. The
Coordinator PM is event-driven and runs throughout L4 implementation.

## Roles

### Planner PM

- **Cadence:** runs in passes — after SA-done; on re-planning triggers (new contract, materialized risk, scope change).
- **Tier:** senior or SA — needs to read architecture.yaml and judge tracer-bullet vs standard, dev tier, reviewer set.
- **Inputs:** suite briefs (PO), architecture.yaml + per-module contracts.yaml + risk register (SA), existing build-plan
  if present.
- **Outputs:** `.jig/plan/build-plan.yaml` (created or updated), `.jig/store/tickets.jsonl` entries.
- **Discovery loop:** same shape as PO and SA — walks an axis (capabilities → tickets), asks operator to confirm tier
  assignments and reviewer-set selections, gates at end.

### Coordinator PM

- **Cadence:** continuous, event-driven.
- **Tier:** standard. Mostly deterministic routing logic with thin LLM wrapping for ambiguous escalation decisions.
- **Inputs:** ticket state events from the bus, dev agent escalation messages, build plan.
- **Outputs:** ticket dispatch commands, escalation routing (to SA / to operator), build-plan status updates, stalled-
  work alerts.
- **Operations (mostly mechanical):**
  - Ticket finished → mark done in build plan → next ticket in order.
  - Dev agent stuck → classify escalation → route to SA (if contract-gap) or operator (if product/scope question).
  - Ticket blocked > N hours → surface to operator.
  - Bones layer of all epics complete → unlock MVP layer.

## Artifacts on disk

```
.jig/
  plan/
    build-plan.yaml             Living: epics, layers, tickets,
                                 status. Owned by Planner PM.
    deferred.jsonl              Notable-severity items deferred from
                                 reviews. Owned by Coordinator PM.
```

## build-plan.yaml — sketch

```yaml
spec_version: 1
project: jig-search
generated_at: 2026-05-01T15:00:00Z
last_revised: 2026-05-01T15:00:00Z
revision: 3

epics:
  - id: catalog-ingest
    title: Catalog ingest from customer systems
    suite: catalog
    modules: [catalog-ingest]
    layers:
      bones:
        status: in_progress
        tickets: [tb-catalog-ingest]
      mvp:
        status: not_started
        tickets: [t-shopify-oauth, t-csv-parser, t-normalize-skus]
      final:
        status: not_started
        tickets: [t-rate-limit-handling, t-partial-failure, t-retry-policy]
    risks_addressed: [r-shopify-delta]

  - id: categorization
    title: SEO categorization of normalized catalog
    suite: catalog
    modules: [categorization]
    layers:
      bones:
        status: not_started
        tickets: [tb-categorization]
      mvp:
        status: not_started
        tickets: [t-seorank-client, t-propose-categories]
      final:
        status: not_started
        tickets: [t-dedupe-categories, t-synonym-matching]
    risks_addressed: [r-seorank-latency]

ordering_rule: bones-first
# Bones layer of ALL epics completes before MVP layer of ANY epic begins.

stalled:
  - ticket: t-shopify-oauth
    reason: blocked-on-spike
    blocked_since: 2026-05-01T12:00:00Z
    spike: spike-shopify-delta

open_questions:
  - id: q-feature-priority
    text: "MVP layer order: catalog before categorization, or in parallel?"
```

## Ticket structure (extensions)

Tickets gain new fields beyond the existing minimal shape:

```yaml
id: tb-catalog-ingest
title: Tracer bullet — catalog ingest end-to-end
type: tracer-bullet            # or "standard" or "spike"
suite_id: catalog
module_id: catalog-ingest
capability_ids: [shopify-connect, normalize-skus]   # what's exercised
epic_id: catalog-ingest
layer: bones                   # bones | mvp | final
estimate: M                    # S | M | L
dev_tier: senior               # standard | senior | sa
reviewer_set:
  - contract-compliance
  - cross-cutting-policy
  - spec-compliance
  - pattern-conformance        # added because cross-module
context_hints:
  always_inject: [cross-cutting-policies]
  auto_inject_uris:
    - project://arch/modules/catalog-ingest/contracts
    - project://arch/contracts/shared/product
    - project://arch/contracts/shared/catalog-ingested
  pull_available: true
risks_addressed: [r-shopify-delta]   # tracer-bullet validates this
done_when: |
  Data flows: customer source → ingest module → normalized record in
  products collection → catalog-ingested-event published → consumer
  receives event payload. Single happy path. No error handling
  required at this layer.
```

For **standard tickets**, `type: standard`, `layer: mvp` or `final`, `done_when` references the behavior + integration
AC the ticket satisfies. For **spike tickets**, `type: spike`, narrow scope, output is a comment with findings, no
production code expected.

## The three completeness layers

| Layer     | What's built                                                                                                                                                         | Done when                                                                                         |
|-----------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| **Bones** | Per-epic tracer bullet. Every module the epic touches; one happy path; no edge cases, no error handling, minimal validation.                                         | Data flows end-to-end across the modules touched. The contracts demonstrably compose.             |
| **MVP**   | Per-epic minimal useful functionality. Real integrations (not mocks). Critical failure modes handled. The handful of capabilities that justify the product existing. | The epic does what its capabilities promise, well enough to demo.                                 |
| **Final** | Per-epic full coverage. Edge cases, polish, performance work, the long tail of behavior AC.                                                                          | Every behavior AC and integration AC is satisfied; reviewer federation passes at full strictness. |

**Ordering rule:** bones layer of *all* epics completes before MVP layer of *any* epic begins. This is the system-level
walking skeleton — without it, "bones-first" degrades into "thoroughly build feature 1 before starting feature 2."

## Tracer bullet tickets vs standard tickets vs spikes

|              | Tracer bullet                             | Standard                        | Spike                                           |
|--------------|-------------------------------------------|---------------------------------|-------------------------------------------------|
| Purpose      | Validate integration across modules       | Implement a behavior            | Answer an architectural unknown                 |
| Scope        | Cross-module, one happy path              | Single capability, full AC      | Narrow exploration, time-boxed                  |
| Output       | Working end-to-end skeleton               | Production code satisfying AC   | Findings comment, no production code            |
| Tier         | Senior (default)                          | Per ticket-tier decision        | Senior (default)                                |
| Layer        | Bones                                     | MVP or Final                    | Pre-bones                                       |
| Reviewer set | Contract + cross-cutting + spec + pattern | Per ticket-tier decision        | Light — "did you answer the question?"          |
| When run     | Bones layer, before any MVP               | MVP or Final layer, after bones | Before SA delta-pass that depends on the answer |

## Reviewer federation — selection logic

Each ticket gets a reviewer set chosen by Planner PM. The default-on subset (always run):

- **Contract compliance** — does the diff abide by the module's contracts.yaml (ownership, schemas, APIs)?
- **Cross-cutting policy** — does it violate any universal rule (PII, secrets, no-direct-cross-module-db)?
- **Spec compliance** — does it satisfy the behavior AC and integration AC the ticket cites?

Add-ons (selected per ticket characteristics):

- **Pattern conformance** — when the ticket adds new code in an area with established patterns; tier: senior.
- **Error handling** — when ticket touches failure modes flagged in integration AC; tier: senior.
- **Test adequacy** — when ticket adds new behavior; tier: standard.
- **Architectural review** — when ticket is SA-tier or amends a contract; tier: SA.
- **Security review** — when ticket touches auth, PII, secrets, payments; tier: senior.
- **Performance review** — when ticket has a perf budget in integration AC; tier: senior.
- **Visual compliance** — when ticket implements UI (has `visual_references.wireframes` set); tier: senior. Vision-based
  diff between implementation screenshot and the wireframe; design-system token / component check at MVP/Final layers;
  accessibility check at Final layer. See `docs/visual-design/design.md` for the visual reviewer mechanics.

Each reviewer runs in parallel on PR completion. Comments are merged by either the orchestrator (mechanical dedup) or a
thin **lead-reviewer** agent (semantic dedup, conflict resolution). The dev agent receives the unified, structured
comment set.

## Comment structure (machine-first)

Every reviewer comment is structured before any prose:

```yaml
- type: contract-violation         # type taxonomy below
  severity: critical               # critical | important | notable
  contract_uri: project://arch/modules/catalog-ingest/contracts#owns/products/write_access
  file: catalog/categorization.py
  line: 47
  confidence: 1.0                  # mechanical = 1.0; judgment < 1.0
  suggested_diff: |
    -    db.products.update_one(...)
    +    catalog_client.update_product(...)
  prose: |
    `categorization` is writing directly to `products`, but the
    contract reserves write access to `catalog-ingest`. Use the
    catalog client API instead.
```

**Types** (mechanical):
- `contract-violation`
- `cross-cutting-policy-violation`
- `spec-violation` (behavior AC or integration AC not met)

**Types** (judgment):
- `pattern-divergence`
- `error-handling`
- `test-adequacy`
- `code-clarity`

Mechanical types have `confidence: 1.0` by definition; judgment types report a probabilistic confidence the dev agent
can use to prioritize.

## Severity tiers and disposition

| Severity      | Disposition                                 | Escalation                             |
|---------------|---------------------------------------------|----------------------------------------|
| **Critical**  | MUST fix before merge                       | None — fix or block                    |
| **Important** | SHOULD fix unless fix triggers major rework | If rework needed → consult SA          |
| **Notable**   | Fix or push to DEFERRED queue               | Operator decides at plan-revision time |

## Auto-apply path

When a Critical comment has both `confidence: 1.0` (mechanical) and a `suggested_diff`, the dev agent may apply it
automatically without operator intervention. Operator only sees fixes that require judgment.

## Bounded fix loops

Cap at **3** review→fix cycles per ticket. If the cycle hasn't converged by then:

- Coordinator PM escalates to operator with: the unresolved comments, the dev agent's stated reason, and the relevant
  contract/AC.
- Possible outcomes: operator overrides ("this is fine"), SA amends contract (was wrong), Planner PM resplits ticket
  (was too big).

## Reviewer self-check before posting

Each reviewer agent reviews its own output before publishing comments. Cheap step that catches false positives. Same
pattern as the implementer self-review before handoff.

## Dev tier × reviewer tier matrix

| Ticket complexity                                          | Dev tier     | Reviewer tier (per agent in set)                                         |
|------------------------------------------------------------|--------------|--------------------------------------------------------------------------|
| Simple CRUD, well-precedented                              | standard     | standard (mechanical reviewers); senior for any judgment reviewer in set |
| Cross-module work, novel integration                       | senior       | senior or SA per reviewer type                                           |
| Foundational pattern, contract-establishing, tracer bullet | senior or SA | SA for architectural review; senior for others                           |

**Rule:** reviewer tier ≥ dev tier on a per-reviewer basis. A standard dev agent never gets reviewed exclusively by
standard reviewers when the ticket has architectural implications — at minimum the architectural reviewer is bumped up.

## Three-tier context model for dev agents

| Tier     | Always-injected                 | Auto-injected when ticket touches                            | Pull on demand               |
|----------|---------------------------------|--------------------------------------------------------------|------------------------------|
| Standard | Cross-cutting policies          | Own module contracts.yaml, integration AC for the capability | (rarely needed)              |
| Senior   | + architectural decisions       | + cross-module shared contracts                              | + related modules' contracts |
| SA       | + risk register, open questions | + full architecture.yaml                                     | + spike outputs, change_log  |

This composes orthogonally with the federated reviewer model: *tier* determines context budget; *context model*
determines which artifacts within that budget; *reviewer set* determines what gets checked at the end.

## Iteration — the build plan is living

The build plan is not write-once. Triggers for a Planner PM delta pass:

- **New PO content** — new journey or capability added → build plan needs to incorporate.
- **Risk materializes** — a spike confirms a risk wasn't hypothetical → contracts amended → tickets that depend on
  amended contract need re-plan.
- **Bones reveals contract gap** — tracer bullet exercises the contracts and surfaces missing or wrong contracts → SA
  delta → build plan delta.
- **Reviewer-loop escalation** — fix cycle won't converge → operator decides ticket needs resplit → Planner PM redoes
  that epic.
- **Operator-initiated** — pivot, descope, repriority.

## Scale-down: every role gets two exits

To make the same workflow work for both small and medium projects (without forcing operator to declare scale upfront):

**Minimal-output exit:** "I have something to say but it fits in 5 lines." Examples:
- L1 PO with 1 persona, 1 journey produces a one-paragraph discovery.md.
- SA writes a 5-line architecture.yaml.
- Planner PM creates 3 tickets with no bones/MVP/final layering — just a flat list.

**Skip-next exit:** "There is literally no work for the next role." Examples:
- L1 PO sees 1 persona, 1 journey → declares L2 trivial → skip L2 organizer.
- SA sees 1 module, no cross-cutting concerns → skip Planner PM, hand directly to Coordinator with auto-generated
  tickets.
- Planner PM has 3 tickets → no need for Coordinator routing → orchestrator dispatches directly.

This is judgment built into each role's prompt, not a separate "small project" mode. A project that starts small and
grows into medium picks up the next role on the next pass without a mode-switch cliff.

## Risks (of this design)

- **Plan staleness.** Build plan diverges from reality as tickets surface gaps. Mitigation: Coordinator PM updates
  status mechanically; Planner PM delta passes on triggers above.
- **Tier mis-classification.** Planner PM under-tiers a ticket and standard agent fails. Mitigation: dev agent can
  request mid-work tier promotion; Coordinator routes the promotion request.
- **Bones gets skipped under pressure.** Operator says "we need feature X by Friday, skip the tracer bullet." This is
  exactly the failure mode the design exists to prevent. Mitigation: skipping bones is an explicit operator override,
  not an accident; build plan records "bones skipped: <reason>" so the consequences are visible if integration later
  breaks.
- **Coordinator PM as bottleneck.** Single agent routing all events; could starve. Mitigation: Coordinator is mostly
  deterministic — runs as cheap fast loop, not LLM-per-event.
- **Build plan grows too big to read.** A 50-ticket plan is hard to operator-review. Mitigation: TUI affordances —
  collapse by epic, by status, by layer; surface only the active layer by default.
- **Plan revision cascades.** Amending one contract triggers re-plan for many tickets. Mitigation: change_log on
  contracts; Planner PM marks affected tickets `stale` rather than auto-rewriting; operator confirms re-plan scope.

## Out of scope

- Multi-team coordination, capacity tracking, calendar-based scheduling.
- Wall-clock estimation. Estimates are S/M/L relative-effort.
- Retrospectives, standups, etc. — human-team rituals.
- Velocity metrics. Useful later; not v1.
- Cross-project portfolio planning. Single-project assumed.

## Open questions

1. **Sprint / iteration unit.** Is "the next batch of tickets to dispatch" a real concept (named, bounded), or just "the
   next item in the plan"? Probably named for operator visibility, but the name shouldn't be "sprint" if we're not doing
   time-boxes.
2. **Estimation calibration.** S/M/L is relative — relative to what? Probably "compared to other tickets in this
   project," but new project has no baseline. Default heuristic needed.
3. **Coordinator PM as deterministic vs LLM-thin.** How much can be pure code vs needs an LLM? Spectrum. Worth
   prototyping both.
4. **Lead-reviewer agent vs orchestrator dedup.** When merging federated reviewer comments, mechanical dedup is cheap
   but misses semantic duplicates. Lead-reviewer agent is more accurate but more expensive. Probably orchestrator does
   mechanical dedup as v1; lead-reviewer is a v2 upgrade.
5. **DEFERRED queue ownership.** Coordinator PM creates entries; who triages? Planner PM at next pass? Operator
   manually? Probably Planner PM in a dedicated re-plan trigger.
6. **Mid-work tier promotion mechanics.** Dev agent says "I need senior context." How does the orchestrator handle —
   restart with new context, or graft new context onto running session?
7. **What if bones never converges?** Tracer bullet keeps surfacing contract issues; SA keeps amending; bones doesn't
   complete. At what point is this a signal that the architecture is fundamentally wrong? Probably 3 SA delta passes
   without bones-success → escalate to operator with "the architecture isn't working; consider redesign."
8. **Auto-promote bones layer when most epics done?** If 4 of 5 epics' bones are done and the 5th is blocked, can MVP
   start on the 4? My instinct is no — bones-first ordering is non-negotiable — but operator override should exist.

## Implementation phases

Not yet planned in detail. Rough order:

1. **Schema** — `build-plan.yaml` Pydantic model, ticket field extensions, deferred.jsonl shape.
2. **Planner PM** — role config, MCP tools (`plan_create_epic`, `plan_add_ticket`, `plan_set_layer_status`,
   `plan_finalize`), discovery-loop prompt parameterized for ticket axis.
3. **Coordinator PM** — mostly daemon-side logic; thin LLM role for ambiguous escalations. MCP tools for dispatch and
   escalation routing.
4. **Tracer-bullet ticket type** — type tag, special context injection, special done-criteria.
5. **Reviewer federation** — agent role per reviewer type with tier variants; orchestrator-side parallel dispatch and
   comment merging.
6. **Auto-apply mechanism** — orchestrator path for mechanical confidence-1.0 fixes.
7. **TUI affordances** — `/plan show`, `/plan revise`, `/deferred list`, `/sprint dispatch`, layer status visualization,
   review comment unified view.
8. **Scale-down judgment** — prompt engineering each role's minimal-output and skip-next exits.

## Change log

- 2026-05-01: Initial draft (brent + claude). Captures: PM split (Planner + Coordinator), build plan with bones/MVP/
  final layers, tracer-bullet ticket type, federated reviewer with severity/type/structured comments, three-tier dev/
  reviewer tiering, three-tier context model, scale-down exits. Many open questions remain.
