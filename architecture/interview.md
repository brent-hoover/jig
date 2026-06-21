# The Operator Interview — Authoring the Living Invariant

The interview is the **authoring half** of the Living Invariant. It and the entity model are **duals**: every
step must *produce* something in the model, and every entity must have a step that produces it. A step that
produces nothing is just conversation; an entity nothing produces can't be authored. Gaps either direction are
findings.

**Phases map onto the two ladders:**

- **Phase 1–2 = the intent ladder** (pitch → personas → journeys → suites/capabilities). Operator + PO own it.
- **Phase 3 = the structure ladder** (boundaries → contracts). The architect owns it; the developer-operator
  approves it (founder: auto-approved).
- They **converge at suites/capabilities** — the handoff from "what" to "how."

## The lens (how every step is logged)

| Field | Meaning |
|-------|---------|
| **Ask** | what Jig elicits from the operator |
| **Produces** | the entity/edge it writes into the model |
| **Locks in** | the invariant it establishes or makes checkable later |

## Pushback — a core mechanic, not a personality trait

Jig **pushes back within reason** to force inputs to populate the seeds later steps decompress.

- **Stopping rule.** Push until the input names a *who*, a *mechanism*, a *bound*, and a *contrast* — then
  stop, even if adjectives remain.
- **Smell taxonomy** (parked — to formalize): vague persona, outcome-adjective-without-mechanism, no contrast,
  unbounded scope. Some are mechanically detectable.

## Cross-cutting: operator persona (flow config, set once)

Not a content step — it configures the *flow*. **Developer** vs **Founder**; design the Developer flow,
Founder is a reduction ("just less" — gates auto-approved, views collapsed to overview). One Living Invariant
either way; persona = depth-of-descent + view density, localized to a few variation points (never pervasive
branching). For the **Jig dogfood**, operator persona coincides with the project's user persona (Jig's product
is a tool for operators) — a property of dogfooding a dev tool, not the general case.

---

# Phase 1 — Pitch

## Step 1 — Elevator pitch

| | |
|---|---|
| **Ask** | "Describe the project in one line — elevator-pitch form." (May be a whole sub-process — getting a founder to be concrete is hard.) |
| **Produces** | The **Project** root node **plus** structured **pillars** (the mechanism, extracted). The line for humans, the pillars for the graph. |
| **Locks in** | Top of the trace spine — the root every Journey/Capability ladders up to. The **scope arbiter**: "which pillar does this serve?" |

- A good pitch is *dense*: "for [persona] it does [value] unlike [alternative]" — seeds later steps decompress.
- The pitch only earns its place if it's **sharp enough to reject things**. First and highest non-goal
  generator.

## Step 1.x — Project non-goals ("not for…")

> **Finding:** non-goals are not in the operator's explicit phase list, but are first-class per tenet 3. They
> are seeded here and refined through Phase 2 — they need a defined home in the phase structure.

| | |
|---|---|
| **Ask** | "Who/what is this explicitly *not* for?" |
| **Produces** | **anti-persona(s)** (`ProductNonGoal`), a **seed persona** (the inverse), sometimes a candidate **project principle**. |
| **Locks in** | Bounds Coverage (gives scope a ceiling); scopes the persona space before personas are developed. |

- Seeded here, refined throughout — non-goals are discovered by contrast.
- A root non-goal can **promote** to a project-wide principle (`CrossCuttingPolicy`) agents cite at forks.

---

# Phase 2 — Requirements (the intent ladder)

End state: all requirements defined (personas + journeys + suites/capabilities + ontology).

## Step 2.1 — Enumerate personas

The project's **user** personas (personas *in* jig — for the Jig dogfood these coincide with the operator
personas developer/founder; in general they're the product's end-users: customer / merchant / …).

| | |
|---|---|
| **Ask** | "Who uses this product?" |
| **Produces** | **Persona** entities (head of the user ladder). |
| **Locks in** | Every persona will need ≥1 journey; orphan personas are findings. |

## Step 2.2 — User journey interview

| | |
|---|---|
| **Ask** | "Walk me, as a narrative, through each persona's end-to-end path through the product." |
| **Produces** | **Journey** entities **and** the **project Ontology** (domain terms captured during journey walks, per `ontology.md`). |
| **Locks in** | Capabilities get *derived from* journeys (not invented); every journey needs a covering Suite. |

## Step 2.3 — System defines suites (+ capabilities)

| | |
|---|---|
| **Ask** | (System-derived from the journey docs, operator confirms.) |
| **Produces** | **Suite** entities — grouped functionality (PO-owned, organizational; distinct from **Module**, SA-owned/architectural). Suites contain **Capabilities**; each capability carries **Behaviors + AC**. |
| **Locks in** | Coverage: every journey covered by a suite; every capability traces to a journey. |

- **Use the word "Suite"** — it's canon in `ontology.md`. Coining a synonym is a tenet-4 violation.
- **User stories are REQUIRED, not optional** (operator override of `ontology.md`). A user story is the
  **narrative form of the trace edges**: "As [persona]" = user-ladder link, "so that [value]" = value-ladder
  link. Requiring one per capability forces both links to be declared → **orphan capabilities impossible by
  construction** (the convergence invariant enforced at *authoring* time) + tenet-4 cognitive scaffolding.
  Capability required fields: **User Story (who/why) + Behaviors + AC (what/done)**.
  > **Finding:** `ontology.md` says "Optional" — contradicts this. Fix the definition at consolidation.

## Resolution decision — how deep before architecture

Granularity at the requirements→architecture handoff is set by **what the System Architect needs**, which is
**architectural facets**, not testable AC:

- what data/state the capability creates/reads/updates → data contracts, ownership
- what it integrates with (external systems, other suites) → external boundaries, contracts
- non-functional demands (latency, concurrency, volume, security) → policies, boundaries
- architectural unknowns / risk → spike

**Decision:** define each capability to **architecturally-sufficient resolution** now (mandatory user story =
trace **+** architectural facets); **defer testable behaviors + AC to just-in-time**, per build layer
(bones → MVP → final). This is Jig's existing **L0–L4 resolution** + **bones/MVP/final**, rediscovered.

- **Resolution is non-uniform / demand-driven**: high-level by default; deeper only where architecture demands
  it (risk/integration hotspots → spike or push-back-up). Tenet 2 ("exact context") applied to requirements
  depth.
- **Reconciles with mandatory user stories**: the user story (trace) is mandatory NOW; the AC (detail) defers.
  Different facets, different times.

A capability at architectural resolution = **user story (trace) + facets {data, integrations, NFRs, risk}**.

---

# Phase 3 — Architecture (the structure ladder)

## Phase 3 is a dialogue — the SA ↔ operator loop

You don't predetermine the resolution the architect needs; the architect **pulls** what it's missing. The
facet checklist (data/integrations/NFRs/risk) is a cheap first pass to minimize round-trips (tenet 2);
"enough" = the architect has no blocking questions.

**Two kinds of "need more," routed by who can possibly know** (tenet 5, "humans live in the real world,
agents don't"):

- **Operator-answerable** (intent/domain the operator knows but didn't say) → architect asks the operator
  (Question/Answer thread entries; `OpenQuestion`).
- **Technical-unknown** (nobody knows yet) → **spike** (bounded exploration), never an operator question.

**Questions are signal, not just input:** a question exposing an underspecified capability → push back up to
refine that capability (tenet 1, "hard to decompose = spec-level smell"). The loop improves requirements, not
only architecture.

**Capture Q&A as durable rationale** (`OpenQuestion`→resolved, `TechDecision`, `ChangeLogEntry`) — it becomes
the "why" inside the Living Invariant.

**Persona variation:** developer participates in the loop; founder gets only must-ask questions, rest
auto-defaulted.

## Step 3.1 — Architect reviews personas + suites

The architect (SA) reads the intent model as input.

## Step 3.2 — Architect proposes the architecture

| | |
|---|---|
| **Produces** | **Boundaries**, **Contracts**, **Modules**, **Dependencies** (+ risks, tech decisions, tradeoffs). |
| **Locks in** | Containment + Conformance; Trace edges Capability `realized_by` Contract/Module. |

> **Finding (open):** the **VD / frontend-architecture** layer (wireframes, screens, design system, frontend
> arch) runs *parallel* to the SA in the full model but is absent from Phase 3 as stated. In/folded/deferred?
> Light for Jig-the-TUI, real in general.

## Step 3.3 — Approval → ready to start

Developer-operator reviews and approves the proposed architecture (founder: auto-approved). On approval the
**requirements-gathering portion is complete** and the project is ready to build (PM build-plan + factory loop
— Phase 4, outside requirements gathering).
