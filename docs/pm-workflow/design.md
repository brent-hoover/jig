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
- **Tier:** standard. Mostly deterministic Python service with thin LLM helpers for the cases where pure code can't
  judge correctly.
- **Inputs:** ticket state events from the bus, dev agent escalation messages, build plan.
- **Outputs:** ticket dispatch commands, escalation routing (to SA / to operator), build-plan status updates, stalled-
  work alerts, auto-escalation triggers (see "Auto-escalation thresholds" below).

**Deterministic operations** (pure code, no LLM):

- Ticket finished → mark done in build plan → dispatch next ticket in cycle order.
- Bones layer of all epics complete → unlock MVP layer.
- Ticket blocked > N hours → surface to operator (structured alert).
- Kind-tagged escalation routing — dev agent posts `escalation_kind: contract_gap` → route to SA; `escalation_kind:
  scope_question` → route to operator. Routing table is static.
- Auto-escalation threshold checks (see below) — purely metric-driven.

**LLM-thin operations** (cheap LLM helper, narrow scope):

- **Unstructured escalation classification.** Dev agent posts free-text "this is weird, I don't know what to do" without
  a `kind` tag. LLM helper reads the comment + recent ticket history + classifies into one of the routing destinations
  (SA / operator / Planner re-plan / tier-promote). Falls back to operator if confidence is low.
- **Cross-ticket pattern detection.** Periodic sweep over recent escalation/failure events asks "is there a pattern here
  that should trigger a re-plan?" — e.g., "tickets touching module X always escalate," "this contract gets amended every
  other ticket." Pattern → flagged to Planner PM as a re-plan trigger.

The LLM helper runs on the smallest capable model with a strict prompt; called only when the deterministic path can't
decide. Pure-deterministic Coordinator was considered and rejected — too brittle for the long tail of weird operational
situations agents put it in.

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

### Tickets per layer — single by default, multiple when justified

The `tickets:` field on each layer is a list to permit multiplicity, but **the default and intent for bones is one
ticket per epic.** A tracer bullet is by definition one thing — one happy-path slice through every module the epic
touches. The natural unit is one ticket: one dev agent owns the slice, holds the integration in their head, makes it
work end-to-end. Splitting bones into multiple tickets risks recreating exactly the failure mode bones-first was
designed to prevent — multiple agents working in parallel, none individually responsible for "does the spine compose."

When a single bones ticket isn't tractable, splits are allowed:

- **Tracer bullet exceeds a single dev agent's scope at the chosen tier.** A tracer through 6 modules at standard tier
  might genuinely not fit; senior tier or a split is the answer.
- **The epic has fundamentally distinct happy paths** that share little code (e.g., a catalog-ingest epic where "Shopify
  happy path" and "CSV happy path" are mostly disjoint). Two narrow tracer bullets are clearer than one fat one trying
  to demo both.
- **Genuinely concurrent integration paths** that are independently testable (read path vs write path through the same
  module).

When bones is split, **the layer still gates as a unit**: MVP doesn't unlock until *every* split bones ticket passes,
not just the first. Otherwise the splits drift back into "thoroughly build feature 1 before starting feature 2," which
is the BDUF failure mode this whole design exists to prevent.

The same applies to MVP and Final layers, but the trade-off is different there: MVP and Final are by nature multi-ticket
(each capability gets its own ticket), so the "single by default" framing is bones-specific.

### Estimation calibration

S/M/L estimates need anchoring. New projects have no baseline; established projects should get more accurate over time.
Two-stage approach:

**Initial heuristic — semantic scope:**

- **S** = one behavior, single file likely, no cross-module concerns, well-precedented pattern.
- **M** = a capability or a small module slice; multiple files; one new contract or extending an existing one.
- **L** = cross-module work, substantial state changes, new shared shapes, or first-of-kind for the project.

Planner PM uses semantic-scope as the default for the first few cycles of a new project.

**Analytics-driven calibration — per project, per tier:**

Once the project has run a handful of tickets, observed cycles feed back into Planner PM's estimation prompts. The
metrics that matter:

- **Turns** (primary) — closest to LLM cost and operator-perceived "work."
- **Tool calls** (secondary) — proxy for "actions taken"; a 30-turn ticket with 5 tool calls is mostly chat; a 30-turn
  ticket with 100 tool calls is real work.
- **Tokens** (tertiary) — for cost forecasting only; high-token doesn't correlate with high-work because of context
  loads.

Wall-clock is deliberately skipped — too noisy, too model-dependent.

Per-tier illustrative bands (calibrated per-project from observed cycles; these are starter values):

| | Standard | Senior | SA |
|---|---|---|---|
| **S** | 5–15 turns / 10–30 tool calls | 5–15 / 10–30 | 5–15 / 10–30 |
| **M** | 15–40 / 30–100 | 20–50 / 40–150 | 30–80 / 60–200 |
| **L** | 40+ / 100+ | 50+ / 150+ | 80+ / 200+ |

Calibration loop: every N completed tickets, the analytics layer computes the actual turn / tool-call distribution per
tier per S/M/L bucket and emits a `EstimationCalibrationUpdated` event. Planner PM reads the latest calibration when
sizing new tickets and adjusts prompts accordingly. Operator can override any auto-calibration via `/plan calibrate`
slash command.

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

### Two-cadence review

The federation runs at two cadences (resolved during the SA open-questions session — see
`docs/sa-architecture/design.md`):

- **Per-commit (light)** — fires within seconds of `git commit`. Runs **only the mechanical reviewers**
  (contract-compliance, cross-cutting-policy, spec-compliance against integration AC). These are deterministic checks
  with confidence 1.0; they don't need vision, don't need codebase pattern analysis, and don't need an LLM judgment
  pass. Catches "you wrote to the wrong collection" before the dev agent compounds the mistake.
- **End-of-ticket (full)** — fires when the dev agent marks the ticket ready-for-review. Runs **the entire reviewer
  set** (mechanical re-run + judgment-flavored: pattern conformance, error handling, test adequacy, security,
  performance, visual compliance, architectural review). This is the federation as designed above.

Mechanical reviewers MUST be implementable as fast deterministic checks (single-digit seconds) so per-commit runs don't
slow the dev loop. SA-side commitment: contracts have to be checkable in this latency budget, not just bulk-evaluable at
end of ticket. Practically, this means contract validation runs against the Pydantic-validated contract schema rather
than an LLM re-derivation.

Per-commit comments use the same structured-comment format as end-of-ticket but are tagged with `cadence: per_commit`.
Auto-apply behavior (see below) applies here too — mechanical confidence-1.0 fixes can apply automatically without
operator intervention.

Per-commit failures emit `PerCommitCheckFailed` analytics events; end-of-ticket failures emit the standard
`ReviewCommentPosted` events. The analytics distinction matters because per-commit-failure rate is a tier-calibration
signal independent of end-of-ticket review noise.

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

The orchestrator wires the federation as a **gate** on ticket
resolution, not an observation hook. After a clean merge the gate runs
`dispatch_with_llm_spawn` and routes the returned comments through
`apply_severity_disposition`:

- **Critical** comments flip the ticket to `FAILED` with the structured
  reason `reviewer-critical`. A `Note` summarising the criticals lands
  on the thread; the worktree + branch are preserved so the operator
  can address the comments and re-run the federation. The ticket does
  NOT mark `RESOLVED`.
- **Important** comments flip the ticket to `BLOCKED` with the
  structured reason `reviewer-important` and post a
  `Handoff(phase="sa-consult")` per comment. The orchestrator's
  existing handoff dispatch path picks the SA reviewer up; the ticket
  unblocks once the SA addresses the consult.
- **Notable**-only comments leave the ticket `RESOLVED` but defer it
  via the Coordinator (the row lands in `.jig/plan/deferred-queue.jsonl`
  with reason `reviewer-notable`).
- **Mixed** severities follow precedence: critical wins over important
  wins over notable. Notables on a non-resolving ticket are not
  deferred — the ticket isn't actually leaving in-flight.

If `dispatch_with_llm_spawn` raises (transient SDK / network error),
the orchestrator retries once after a 2-second delay. If the retry
also fails the ticket is marked `FAILED` with reason
`federation-error` and a `Note` describing the failure — a misbehaving
federation must never silently pass a ticket the operator expected
gated.

The gate is controlled by `orchestrator.run_review_federation` in
`.jig/config.yaml`. The flag defaults `True` so the gate ships on
every project; operators who want the legacy passive
(observation-only) behaviour set the flag to `False` explicitly per
project.

## Auto-apply path

When a Critical comment has both `confidence: 1.0` (mechanical) and a `suggested_diff`, the dev agent may apply it
automatically without operator intervention. Operator only sees fixes that require judgment.

## Bounded fix loops

Cap at **3** review→fix cycles per ticket. If the cycle hasn't converged by then:

- Coordinator PM escalates to operator with: the unresolved comments, the dev agent's stated reason, and the relevant
  contract/AC.
- Possible outcomes: operator overrides ("this is fine"), SA amends contract (was wrong), Planner PM resplits ticket
  (was too big).

## Auto-escalation thresholds (forced escalation when agents underclaim being stuck)

Dev agents systematically underclaim "I'm stuck." They'll loop on a problem rather than admit blockedness, often
producing more work that doesn't move the ticket forward. Voluntary escalation isn't enough; the system has to *force*
escalation when objective signals show thrashing.

**Coordinator PM monitors these thresholds** continuously per ticket. When any trips, the dev agent is force-escalated
regardless of whether it asked for help — pulled from the ticket, partial work preserved in the worktree, escalation
report assembled, ticket re-dispatched at the next tier (or surfaced to operator if already at SA tier).

| Threshold | Signal | Why |
|---|---|---|
| **Repeated same-failure** | 3+ consecutive `PerCommitCheckFailed` events on the same `contract_uri` | Same mistake repeated; agent isn't learning from per-commit feedback. |
| **Tool-call flailing** | Tool-call success rate < 50% over the last 10 calls | Agent is making errors faster than progress. |
| **No-commit drift** | No commit in the last 30 turns despite active LLM activity | Agent is chasing tail without producing artifacts. |
| **Out-of-budget** | Total turns exceed 2× the tier's expected envelope for the ticket's S/M/L estimate | Agent is well past where calibration says it should be. |

Plus a self-reflection prompt:

- **Forced reflection every 20 turns.** Coordinator injects a structured prompt to the dev agent: *"Honest
  self-evaluation: are you making progress toward the ticket's done-criteria? If no, escalate or request tier
  promotion."* If the agent reports "no" twice in a row, it's auto-escalated regardless of other thresholds.

When auto-escalation fires, the **escalation report** is mandatory — the structured form includes:

- `failed_attempts` — list of approaches the agent tried, with outcome per attempt.
- `last_error` — most recent concrete failure (test failure, contract violation, etc.).
- `agent_hypothesis` — agent's current best-guess about what's going wrong.
- `partial_work_summary` — what the agent did successfully so far (so the next-tier agent doesn't redo it).
- `trip_signal` — which threshold tripped, with the metric value.

The new-tier agent reads this report first; doesn't repeat the failed paths; either continues from the partial work or
starts fresh with the higher-tier context. If escalation fires and the dev agent is already at SA tier, the report goes
to the operator with `tier: sa, no_higher_tier_available` flagged — operator decides next steps.

The thresholds are tunable. If false escalations dominate (agents who are actually progressing get pulled), bump the
numbers. Analytics events (`AutoEscalationTriggered`) feed that judgment.

## Reviewer self-check before posting

Each reviewer agent reviews its own output before publishing comments. Cheap step that catches false positives. Same
pattern as the implementer self-review before handoff.

## Dev tier × reviewer tier matrix

| Ticket complexity                                          | Dev tier     | Reviewer tier (per agent in set)                                         |
|------------------------------------------------------------|--------------|--------------------------------------------------------------------------|
| Simple CRUD, well-precedented                                                                                  | standard | standard (mechanical reviewers); senior for any judgment reviewer in set |
| Cross-module work, novel integration                                                                           | senior   | senior or SA per reviewer type                                           |
| Tracer bullet (foundational, integration-validating) — shared shapes already exist in code                     | senior   | SA for architectural review; senior for others                           |
| Tracer bullet (foundational, integration-validating) — shared shapes need authoring as part of the bones work  | **SA**   | SA across the federation                                                 |

**Rule:** reviewer tier ≥ dev tier on a per-reviewer basis. A standard dev agent never gets reviewed exclusively by
standard reviewers when the ticket has architectural implications — at minimum the architectural reviewer is bumped up.

### Bones tier defaults — bias toward over-spec

Bones tickets are foundational by design (cross-module, integration-validating). Tier defaults:

- **Default = SA tier** when the bones ticket touches shared shapes that don't yet exist in code (Pydantic models, SQL
  schemas, event payloads, shared contracts referenced by multiple modules). The SA agent acts in *scaffold-then-bones*
  mode: writes the shared shapes, wires the integration, makes the tracer-bullet data flow happen. This IS the bones
  implementation. MVP tickets pick up the scaffolded structure and add real logic.
- **Senior tier** when the shared shapes already exist (because SA discovery already authored them) and the bones ticket
  is wiring them through cross-module. Senior dev does the wiring against existing scaffolding.
- **Standard tier** only for genuinely run-of-the-mill bones — single-suite project, no shared shapes, no cross-cutting
  concerns, basically CRUD. Rare in practice.

**Bias toward over-spec.** If the SA isn't sure whether shared shapes need authoring, default to SA tier. Cost of
over-tiering a bones ticket is one extra senior/SA invocation; cost of under-tiering is bones-doesn't-converge
escalation cycles + architectural rework. The asymmetry favors over-specification.

This shift has implications for SA workflow timing — SA isn't fully "done" before PM dispatches; SA continues
participating in bones tickets that need scaffolding. The SA design reflects this in its sequencing diagram.

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
- **DEFERRED queue triage** — see below.
- **Operator-initiated** — pivot, descope, repriority.

### DEFERRED queue triage

The DEFERRED queue (`.jig/plan/deferred.jsonl`) accumulates Notable-severity items pushed forward from review.
**Coordinator PM creates entries** when a reviewer's Notable comment gets pushed there at PR resolution. **Planner PM
triages at re-plan time** — every Planner pass includes a deferred-queue review step. Per item, the Planner picks one
of:

- **Spin into a new ticket** — promote the deferred item to a real ticket in an upcoming cycle.
- **Merge with an existing ticket** — fold the work into a related planned ticket.
- **Drop** — the item is no longer relevant (the code it referred to has changed, the concern was superseded).
- **Leave deferred** — still relevant but lower priority than what's planned; revisit at next re-plan.

Operator override always available via slash commands:
- `/deferred review` — list current deferred items with their context
- `/deferred promote <id>` — force-promote to a ticket out of cycle
- `/deferred drop <id>` — drop without waiting for Planner pass
- `/deferred merge <id> <ticket-id>` — fold into an existing ticket

### Bones-first ordering and operator override

**Default: strict bones-first.** Bones layer of *every* epic completes before MVP layer of *any* epic begins. This is
non-negotiable as the default — losing it loses the integration-validation property bones-first exists to provide.

**Per-epic operator override** when a remaining bones is genuinely low-risk to the rest of the work:

- Slash command: `/plan unblock <epic-mvp>` allows MVP work to start on epics whose bones is complete, while one or more
  other epics' bones continues.
- Override emits a `bones_promoted_incomplete` analytics event so consequences are visible later if the unfinished bones
  forces a contract change that affects already-built MVP work.
- Operator takes the risk consciously and the system records the trade-off.

**`cascade_risk_low` flag — system-suggested promotion when SA judges low risk.**

When the SA looks at the still-running bones tickets and judges "this won't reshape what's already been built — it
touches no new shared shapes, no new contracts, no flagged risks," they mark the ticket `cascade_risk_low: true`. With
the flag set, Coordinator PM actively suggests promotion to the operator:

> 4 of 5 bones tickets complete; bones for `epic-categorization` is still in flight but SA marked it
> `cascade_risk_low: true` (no new shared shapes, no new contracts). Promote MVP on the other 4? (yes / no / wait)

The flag makes the leeway *systematic* rather than purely operator-judgment, while keeping bones-first non-negotiable as
the default. In practice, most still-running bones won't qualify for the flag — that's fine; the absence of the flag
means "operator promotes anyway only if they want to take the risk consciously."

### When bones doesn't converge — "the architecture isn't working" escalation

If a bones ticket triggers SA delta-amendment cycles repeatedly (the SA keeps amending contracts in response to
implementation surfacing gaps, but bones still doesn't pass), it's a signal that something more fundamental is wrong.
Threshold:

- **2 SA delta passes on the same epic without bones convergence** → escalation. Lower than the SA design's "3 cycles"
  threshold for general SA-amendment cascades because we're already at SA tier here — if the SA agent itself can't make
  the bones work after two attempts, the problem is structural.

The escalation is **structured** so the operator gets a usable picture rather than just "this isn't working":

- `failed_bones_attempts` — list of attempts, each with: tracer-bullet path tried, contracts amended during the attempt,
  the specific failure mode, SA's hypothesis about why it failed.
- `current_contract_state` — diff from the SA's original contracts to where they are now after amendments.
- `affected_modules` — which modules the bones touches and which were the locus of failures.
- `sa_root_cause_guess` — SA's best-guess about the structural problem.

Enumerated operator response options (the system orchestrates whichever the operator picks):

- **SA-redesign affected modules** — SA agent re-enters discovery for the specific modules; existing module contracts
  marked stale; bones ticket pauses pending redesign.
- **PO-redesign requirements** — the spec is asking for something architecturally infeasible; PO discovery re-enters for
  the affected capabilities.
- **Descope the epic** — remove from build plan; mark capabilities as deferred to a later release.
- **Try a different bones approach** — same epic, different tracer-bullet path (operator-suggested or SA-proposed
  alternatives).

This is the v2 catch for "bones-first as a discipline runs into reality." It doesn't mean the discipline failed — it
means the system has a structured way to surface and resolve when discipline can't get past a structural problem.

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
- Velocity metrics. Useful later; not v2.
- Cross-project portfolio planning. Single-project assumed.

## Resolved decisions

Locked during 2026-05-03 working session:

1. **Cycle (not "sprint" or "wave")** — the named, bounded "next batch of tickets to dispatch" is a real concept; named
   **cycle** as a hat-tip to Shape Up, no time-box implication.
2. **Estimation calibration** — initial heuristic is semantic scope (S = one behavior, M = a capability, L =
   cross-module). Analytics-driven calibration kicks in once observed cycles exist; per-tier per-S/M/L bands derived
   from turn / tool-call counts (tokens secondary, wall-clock skipped). Calibration loop emits
   `EstimationCalibrationUpdated` events. See "Estimation calibration" section above.
3. **LLM-thin Coordinator** — mostly deterministic Python service; LLM helpers only for unstructured-escalation
   classification + cross-ticket pattern detection. Pure-deterministic was rejected as too brittle for the long tail of
   operational situations.
4. **Orchestrator mechanical dedup for federated reviewer comments in v2; lead-reviewer agent in v2.x** if the
   mechanical approach proves insufficient. Cheap, deterministic dedup ships first; semantic dedup deferred.
5. **DEFERRED queue: Coordinator PM creates entries, Planner PM triages at re-plan time, operator manual override via
   `/deferred` slash commands.** See "DEFERRED queue triage" section above.
6. **Mid-work tier promotion: restart with new tier, partial work preserved in worktree, structured escalation report
   mandatory.** New-tier agent reads the report first to avoid repeating failed paths. Plus auto-escalation thresholds
   (see "Auto-escalation thresholds" section) because agents systematically underclaim being stuck.
7. **Bones-doesn't-converge: 2 SA delta passes on the same epic → "architecture isn't working" escalation** (lower
   threshold than the SA design's 3-cycle general threshold because bones is already at SA tier). Structured trace to
   operator with enumerated response options (SA-redesign / PO-redesign / descope / try different bones path). See "When
   bones doesn't converge" section above. Also: bones tier defaults bias toward over-spec — SA-tier when shared shapes
   need authoring, senior when they exist, standard only for genuinely-CRUD bones.
8. **Strict bones-first default + per-epic operator override (`/plan unblock`) + `cascade_risk_low` flag for
   SA-suggested promotion.** With the flag, system actively suggests "promote MVP on the others while this bones
   continues" when SA judges low cascade risk. Without the flag, operator promotes anyway only if they want to take the
   risk consciously. Both paths emit `bones_promoted_incomplete` analytics events. See "Bones-first ordering" section
   above.

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
- 2026-05-03: Added "Tickets per layer — single by default, multiple when justified" subsection to clarify that the
  `tickets:` list on a layer permits multiplicity but bones intent is one ticket per epic. Splits allowed for
  tractability (single agent scope), distinct happy paths, or concurrent integration paths — but the layer still gates
  as a unit (MVP unlocks only after every split bones ticket passes).
- 2026-05-03: Worked through the eight open questions; all resolved. Locked: cycle naming (Q1), estimation calibration
  with semantic-scope initial + analytics-driven ongoing (Q2, with per-tier turn/tool-call bands as a new section),
  LLM-thin Coordinator with explicit deterministic vs LLM-helper operations breakdown (Q3), orchestrator mechanical
  dedup in v2 + lead-reviewer in v2.x (Q4), DEFERRED queue triage flow (Q5), mid-work tier promotion as
  restart-with-preserved-worktree + auto-escalation thresholds + forced reflection prompt (Q6 — new "Auto-escalation
  thresholds" section), bones-doesn't-converge as 2-SA-delta threshold + structured trace + enumerated operator response
  options (Q7 — new "When bones doesn't converge" section, plus bones-tier defaults reframed to bias toward over-spec
  with SA-tier as default for bones touching unauthored shared shapes), strict bones-first + per-epic override +
  `cascade_risk_low` flag for SA-suggested MVP promotion (Q8 — new "Bones-first ordering" subsection). Open questions
  section converted to "Resolved decisions." Coordinator PM role description expanded with explicit deterministic vs
  LLM-thin operation lists. Dev tier × reviewer tier matrix updated with the new SA-tier-bones row. New analytics event
  types implied: `EstimationCalibrationUpdated`, `AutoEscalationTriggered`, `BonesPromotedIncomplete` — should land in
  `jig/analytics/events.py` next.
