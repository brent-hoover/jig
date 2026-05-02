---
title: Agent Leverage — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-01
---

# Agent Leverage — Problem Statement

## Context

The bulk of the jig design (multi-level spec, SA architecture, PM workflow) is grounded in human-team patterns with
safety rails added to prevent agent failures. That's the right foundation, but it under-exploits the things agents can
do that humans literally cannot:

- Massive parallelism without coordination cost
- Counterfactual exploration (try multiple approaches cheaply)
- Adversarial pairing without the social tax
- Always-on background work
- Perfect cross-reference discipline
- Translation between formalisms (one source → many renderings)
- Structured uncertainty propagation
- Re-derivation from scratch as a sanity check

This doc captures six commitments — discrete features that exploit those capabilities — that should land alongside or
shortly after the 2.0 baseline. Not moonshots; commitments. None individually super-hard; together they substantially
change what jig is *for*.

## Problem

The 2.0 design as captured in the other docs ships a system that's better than yet-another-agent-harness in the
*coherence* dimension (bones-first, contracts, federated review, multi-level spec). It does not yet ship a system that's
better in the *leverage* dimension — exploiting agent capabilities humans don't have. Without these features, we ship a
faithful translation of human dev practice. With them, we ship something agents can do that human teams cannot.

## The six commitments

### 1. Intent as a first-class artifact (refined)

**What:** Replace the flat `rationale` framing with a **disciplined sequence** captured in spec, contract, ticket, and
plan schemas. Each authoring agent fills:

```yaml
problem: "What's this artifact solving?"
simplest_solution: "Most obvious dumb thing that would solve it."
complications_considered:
  scale: "<does it apply? what does it force?>"
  concurrency: "<...>"
  failure_modes: "<...>"
  cross_cutting: "<...>"
  # plus problem-specific complications as needed
```

The flat `rationale` field lets agents fill thinly. The sequence forces a chain of thought where each step has to do
real work — and specifically forces the agent to articulate the simplest baseline before earning any complexity. That's
the cognitive scaffolding move: structured language doesn't just *describe* the work, it *shapes* the work that produces
it.

**Why it matters:** Human teams write this down least and need it most. Forcing the *simplest_solution* step short-
circuits the agent's most common failure mode — jumping straight to the elegant-engineering answer pulled from training
data, skipping the simplification step. Every complexity in the proposed solution has to be earned by an explicit
complication.

Once captured, every subsequent change can be evaluated against original intent: "you're amending this contract — does
the change still serve the original problem? does the simplest solution still apply, or did a complication change?"
Powers retrospectives, heuristic mining, contract amendment cascades, and the eventual learnings layer.

**Generalizes to:**

- **SA contracts** — problem / simplest contract / what scale-or-concurrency-or-failure-mode forces more.
- **PM ticket sizing** — problem / simplest fix / why bigger.
- **Risk evaluations** — problem / simplest mitigation / why insufficient.
- **Spike outputs** — problem we tested / simplest finding / what complications surfaced.
- **Design docs themselves** — see updated `docs/_templates/problem.md`. The discipline applies all the way up.

**Cost to add:** Schema change + prompt-engineering discipline so agents fill the sequence non-trivially. Small.

**Risk:** Agent fills boilerplate at every step. Mitigation: reviewer agent checks for length, uniqueness, and citation
density per step; specifically flags `simplest_solution` fields that look like restatements of the actual proposal
rather than genuine simpler alternatives.

**When:** During 2.0 build, alongside the schemas it attaches to. The cheapest high-value addition — and arguably more
valuable than originally framed because it shapes the *quality of agent thinking*, not just the artifact contents.

### 2. Synthetic operator simulation (workflow testing infra)

**What:** An LLM-driven operator simulator that runs through full project lifecycles in scripted scenarios. Generates
project briefs, walks PO discovery, confirms gates, injects realistic operator behaviors (overrides, mid-stream pivots,
bad inputs).

**Why it matters:** Without it, we ship a workflow design validated only by intuition and a handful of real runs. With
it, every design choice from this point forward gets A/B tested across hundreds of synthetic projects. Quality
multiplier on every subsequent design. *Testing infrastructure, not a feature.*

**Cost to add:** Real engineering — multiple operator personas, scenario library, success-criteria tracking, coverage
metrics for which workflow paths got exercised.

**Risk:** False confidence. Easy to build a simulator that passes scenarios real operators trivially break. Mitigation:
explicit "realism budget" — keep a queue of real-world operator behaviors that surprised the simulator and grow the
simulator to cover them. Track simulator-vs-real divergence as a metric.

**When:** Parallel with 2.0 build, not after. Built early so workflow design choices get tested as we make them.

### 3. Quartermaster agent

**What:** Continuous background agent reading the analytics event stream. Produces periodic operator-facing briefings:
"this week: 12 tickets completed, auth module showing repeated escalations, here are 3 things I think need your
attention."

**Why it matters:** Inverts the operator-as-bottleneck assumption. Operator becomes a strategic reviewer pulled in when
the system surfaces something worth attention, not when every gate fires. Single biggest UX shift toward
agent-as-strategic-partner.

**Cost to add:** Modest. Reads the event stream (already landing), produces structured briefings, surfaces in TUI.

**Risk:** Briefing fatigue — operator stops reading because the briefings are noise. Mitigation: every briefing has a
"was this useful?" feedback path; quartermaster prompt-tunes on the feedback over time. (This is itself a candidate
heuristic-mining application once the learnings layer lands.)

**When:** Once analytics wiring is done. Direct dependency on the event stream.

### 4. Adversarial pairing (skeptic shadow)

**What:** A "skeptic" agent paired with every dev agent on high-tier tickets. Skeptic's only job is "find what could go
wrong with what's being built right now." Two flavors:

- **Periodic checkpoint** (start here): dev pauses at natural breakpoints (after planning, after first meaningful
  commit, before handoff to review); skeptic critiques; dev iterates. Dramatically simpler.
- **Continuous shadow** (later if periodic pays off): skeptic watches dev's tool calls and outputs continuously, posts
  concerns to a thread the dev consults during work. More powerful, more complex.

This subsumes the "twin mode for high-tier tickets" idea — both are "second agent shadow," differing in continuous vs
duplicative.

**Why it matters:** Replaces some review cycles with built-in skepticism. Catches things review can't because review
fires too late and works from finished code, not from the work-in-progress reasoning.

**Cost to add:** Periodic flavor: spawn skeptic agent at checkpoints, give it the dev's outputs, capture its concerns as
a structured artifact the dev consults. Doable.

**Risk:** Pair latency — every checkpoint blocks waiting for skeptic. Mitigation: skeptic is fast (small model, tight
context); checkpoints are bounded.

**When:** After the basic dev/review loop is running so we have a baseline to A/B against.

### 5. Ensemble decision-making at high-stakes points

**What:** For high-stakes decisions (tier classification, contract authoring, escalation routing), spawn N agents with
varied prompts in parallel; vote, merge, or synthesize. Cost: 3-5x on those decisions; quality lift: substantial on the
long-tail of confidently-wrong outputs that single-agent can't catch itself making.

**Why it matters:** The single biggest failure mode of agents is high-confidence wrong outputs. Ensembles surface
disagreement; disagreement is signal. Humans can't ensemble themselves; agents trivially can.

**Cost to add:** Orchestrator extension to spawn N for flagged decisions; synthesizer agent or voting logic.

**Risk:** Cost. Mitigation: only fire on decisions tagged as high-stakes (operator-override frequency, escalation rate,
or explicit tag).

**When:** After we have operator-override telemetry. Knowing which decisions to ensemble requires knowing which ones
we're getting wrong.

### 6. Translation between formalisms

**What:** A "renderer" capability — given any contract / spec / behavior, render it as: Pydantic model, SQL schema,
OpenAPI spec, sequence diagram, operator-facing explanation, test fixture. All from one source of truth.

**Why it matters:** Eliminates synchronization burden between artifact representations. Today: spec is in YAML, code in
Python, docs in markdown, tests separately. Drift is inevitable. With renderers: one source, N derived views, all
guaranteed in sync.

**Cost to add:** Per-renderer prompt + validation. Modular — ship 1-2 renderers initially, add more as needed.

**Risk:** Renderer hallucinations. Mitigation: every rendered artifact has the source contract URI as a header; operator
can compare; round-trip tests where possible.

**When:** Opportunistic. Each renderer pays for itself when the synchronization cost it eliminates exceeds the build
cost. Track that explicitly.

## The unifying meta-shift

These six features together change one assumption: **the operator is in the loop on every decision.** Today's design
treats every gate as an operator confirmation point. With the quartermaster + ensemble + adversarial pairing, most gates
become agent-decided with operator getting a digest. The operator becomes a strategic reviewer pulled in when something
warrants attention, not an operational gatekeeper firing on every transition.

That's the qualitative shift. Everything else is incremental; this is what makes jig a different *kind* of tool.

## Sequencing

| Item | When | Dependency |
|---|---|---|
| 1 — Intent layer | During 2.0 build | None — ride along with schema work |
| 2 — Synthetic operator | Parallel with 2.0 build | None — testing infra; built independently |
| 3 — Quartermaster | After analytics wiring lands | Event stream populated |
| 4 — Adversarial pairing (periodic) | After basic dev/review loop runs | Baseline to A/B against |
| 5 — Ensemble decisions | After operator-override telemetry exists | Data on which decisions to ensemble |
| 6 — Translation renderers | Opportunistic | Per-renderer cost-justified individually |

## Non-goals (in this doc)

- Continuous-shadow adversarial pairing. Designed only after periodic flavor proves out.
- Fine-tuning lightweight specialist models on project corpus. Real but speculative; revisit when there's data.
- Letting agents propose changes to jig itself based on cross-project analytics. Same risk profile as automated
  tool-level learning — operator-in-the-loop only for changes to the tool.
- Multi-agent debate between SAs / reviewers as a standalone feature. Subsumed by ensemble decision-making (#5).

## Open questions

1. **Synthetic operator personas — how many, what shapes?** Probably 3-5 to start: methodical-and-thorough, fast-and-
   shippy, scope-creeper, ambivalent-and-vague, hostile. Each tests different workflow paths. Real shape TBD.
2. **Quartermaster cadence.** Daily? Weekly? On-demand? Probably configurable per operator, default weekly.
3. **Ensemble synthesis algorithm.** Vote? Confidence- weighted merge? Pick the strongest single answer? Run a
   synthesizer agent over all N outputs? Probably depends on decision type.
4. **Adversarial pairing checkpoint frequency.** Too few: skeptic catches things too late. Too many: pair latency
   dominates. Probably tier-dependent.
5. **Intent field discipline.** How do we ensure agents write meaningful rationales rather than boilerplate? Length
   heuristic? Comparison to other rationales for uniqueness? Rationale-quality reviewer agent?
6. **Renderer order.** Which renderer ships first? Probably Pydantic-from-data-contract since we're already using
   Pydantic everywhere.

## Implementation phases

Detailed implementation plans land per-item, not in this doc. This doc commits to the *what* and the *order*; the *how*
gets designed when each item starts.

## Change log

- 2026-05-01: Initial capture (brent + claude). Six commitments locked: intent-as-artifact, synthetic operator
  simulation, quartermaster, adversarial pairing (periodic start), ensemble decisions, translation renderers. Sequencing
  tied to dependencies. Synthetic operator flagged as testing infrastructure that lands EARLY (not late) because it's a
  quality multiplier on every other design choice.
- 2026-05-01: Item #1 (intent layer) refined from a flat `rationale` field to a disciplined sequence (problem / simplest
  solution / complications considered). The sequence forces a chain of thought where each step does real work; flat
  fields let agents fill thinly. The simplest-solution step specifically catches the most common agent failure — jumping
  to elegant-engineering answers and skipping simplification. Generalizes to SA contracts, PM sizing, risk evaluation,
  spike outputs, design docs (see updated `docs/_templates/problem.md`).
