# Dogfood — Jig's Own Living Invariant

Jig run through its own interview. This is both the product test (can the model describe a real, messy system?)
and Jig's actual self-description. Generated live as we walk the interview.

## Values hierarchy (reconciled with TENETS.md / FIRST_PRINCIPLES.md)

Reading the existing docs corrected an overclaim. I had written "primary value is simplicity & clarity," but
`TENETS.md` is explicit: **correctness — does the app actually do what it's supposed to — is the bar.** These
aren't rivals once stacked:

- **Bar (the goal):** *correctness.* The built app does what it's supposed to. (TENETS, "Why Jig Exists")
- **Mission (how, on non-trivial projects):** *coherence.* Keep each agent task small enough to hold in one
  window of attention, and make the small tasks add up to a whole. (TENETS tenet 1, "Bite-sized work,
  coherent whole")
- **Value / aesthetic (what keeps coherence achievable and the system livable):** *simplicity & clarity* —
  the thing that triggered this whole effort.

Causal chain: **simplicity/clarity → coherence → correctness.** The Living Invariant exists so agents never
have to make architectural calls mid-implementation — that's how small tasks stay correct *and* add up.

**The Living Invariant is not a new idea — it formalizes `FIRST_PRINCIPLES.md` law 3:** "the product spec is
one living, breathing doc that AGENTS use to work off of." This effort makes that law *enforced and
reconciled* instead of merely stated.

**Meta-irony, now doubled:** capturing this, I almost wrote a *competing* statement of Jig's primary value
right next to TENETS — a fresh one-concept-two-homes violation. Caught only by reading first. That is the
"one concept, one home" invariant working *by hand*; the goal is to make it work mechanically.

## Step 1 — Pitch

**Pitch (v3):**

> Jig is an application that helps developers and founders build high-quality, well-architected,
> small-to-medium size projects that are reliable and maintainable by using agent-tailored architectural
> processes and a focus on code quality that is enforced at every level, driven by real evals rather than
> vibes.

Passes the stopping rule: persona ✓ (developers + founders), scope bound ✓ (small-to-medium), mechanism ✓
(agent-tailored architectural processes; enforcement at every level), contrast ✓ ("rather than vibes").

**Pillars (the mechanism, extracted — these seed top-level Capabilities):**

1. **Agent-tailored architectural process** — the authoring + factory side.
2. **Multi-level enforcement** — "enforced at every level" (mechanical → reviewer).
3. **Eval-driven quality** — "real evals rather than vibes" (the *measurement* face; ties to the Metrics
   store). Distinct from reconciliation: reconciliation asks "does code match the declared architecture?";
   evals ask "is the architecture any good, and is enforcement actually working?"

> The dogfood loop closed on itself: pushing on Jig's pitch surfaced that **Jig's own differentiator is the
> Living Invariant** — the exact feature being scoped.

## Step 1.5 — Non-goals

**Anti-persona:** "not for people who just want to get something/anything up quickly." Deliberately rejects
the instant-app market (Bolt, Lovable, v0, raw vibe-coding). Coherent with the pitch (inverse of "rather than
vibes").

Two axes fused in that one line (they reject different people):

1. **Values axis** — doesn't care how it's built; wants a result at any cost.
2. **Patience / investment axis** — won't put in upfront effort; wants instant gratification.

The interview *itself* enforces axis 2: a push-back-laden discovery process is a filter — someone who won't
sit through it self-selects out before writing any code. **The process is the gate.**

Durable encoding (so a future *faster* Jig doesn't wrongly exclude quality-minded users): **"doesn't value
durable quality, and won't invest to get it."**

**Seed persona (the inverse):** someone who **values durable quality enough to invest upfront effort.** →
defining trait of persona #1.

**Candidate project principle:** *"Durable quality over speed."* Promote from non-goal to an enforced
`CrossCuttingPolicy` agents cite at every speed-vs-quality fork.

## Step 2 — Operator persona

**Developer** (design target). Founder = reduction ("just less"). For the Jig dogfood, operator persona
coincides with the product's user persona. Flow detail in `interview.md` Phase 2.

## Phase 2 output — suites (derived from Jig's journey = the phase flow)

**Discovery**, **Architecture**, **Build**, **Enforcement**, **Reconciliation**, **Evaluation**,
**Operator Experience**. (Last four cross-cutting; may fold the invariant three into one "Living Invariant"
suite.)

## Phase 2 output — capabilities at architectural resolution

Each = user story (trace) + facets {data, integrations, NFRs, risk}. Testable behaviors/AC deferred to build
layer.

### Suite: Discovery

| Capability | User story | Data | Integrations | NFR | Risk |
|---|---|---|---|---|---|
| Capture pitch | As a dev, I want to state my project in one line and have Jig sharpen it, so the project has a clear scope arbiter | Project root + pillar seeds → spec store | none | interactive | low |
| Elicit non-goals | As a dev, I want Jig to draw out what it's NOT, so scope has a ceiling | ProductNonGoal + candidate principles → spec store | none | interactive | low |
| Enumerate personas | As a dev, I want to define who uses the product, so every capability traces to a real user | Persona → spec store | none | interactive | low |
| Conduct journey interview | As a dev, I want Jig to walk each persona's journey, so capabilities derive from real usage | Journey + project Ontology → spec store | none | interactive, **stateful/resumable** | **med** (multi-turn state; thinking-exercise quality) |
| Capture project ontology | As a dev, I want my domain terms captured once, so every agent uses my vocabulary | Ontology/domain terms → spec store (`.jig/spec/ontology.md`) | read by all later agents | low | med (vocabulary-consistency enforcement is cross-suite) |
| Derive suites & capabilities | As a dev, I want Jig to group journeys into suites/capabilities, so requirements are complete | Suite + Capability + UserStory → spec store | feeds Architecture | low | med (LLM-driven derivation; coverage quality) |

**Architectural signal for the SA:** Discovery is a mostly self-contained suite that writes the intent model
to the spec store; conversational/interactive; the journey interview is **stateful (persist/resume)**; no
external integrations; feeds Architecture. Hard parts: stateful multi-turn interview + LLM-derivation quality.

### Suite: Architecture

| Capability | User story | Data | Integrations | NFR | Risk |
|---|---|---|---|---|---|
| Ingest intent model | As a dev, I want the architect to read personas/suites/capabilities, so the architecture is grounded in requirements | reads spec store | spec store | low | low |
| Propose modules & boundaries | As a dev, I want suites mapped to modules with boundaries, so the system has clear units | Module/Boundary → arch store | reads spec | low | med (core design judgment) |
| Define contracts | As a dev, I want a contract at each boundary, so pieces compose | Contract (API/Event/Data/Behavioral) → arch store | none | low | med |
| Ask the operator (the loop) | As a dev, I want the architect to ask me when intent is underspecified, so I fill gaps instead of the agent guessing | Question/Answer → thread | **operator** | interactive | low |
| Trigger spikes | As a dev, I want technical unknowns explored via bounded spikes, so architecture isn't blocked on guesses | Spike ticket + learning | Build/Factory | low | med |
| Flag risks & open questions | As a dev, I want risks and open questions surfaced, so unknowns are explicit | Risk, OpenQuestion → arch store | operator/spike | low | low |
| Produce architecture for approval | As a dev, I want to review and approve the architecture, so I stay in control (founder: auto) | arch doc + approval | **operator (gate)** | low | low |

**Architectural signal for the SA:** first suite with a **bidirectional operator loop** (Ask the operator /
approval gate) and a **dependency on Build/Factory** (spikes). Two kinds of "need more": operator-answerable →
ask; technical-unknown → spike.

### Remaining suites

Build, Enforcement, Reconciliation, Evaluation, Operator Experience — decompose next, one suite at a time.
