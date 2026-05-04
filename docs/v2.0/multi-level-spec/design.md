---
title: Multi-level Spec — Design
type: design
status: draft
owner: brent
created: 2026-04-30
problem: ./problem.md
---

# Multi-level Spec — Design

## Summary

Replace the single-brief workflow with a tiered one. Five levels of resolution; each level has a verifiable "done"
boundary, dedicated artifact on disk, and a dedicated PO mode (or, at L3, the existing simple-brief PO scoped to one
suite). Discovery (L1) is journey- driven so every capability traces back to a persona's narrative. Suite work (L3)
reuses today's brief format intact.

> **Term note.** Throughout this doc, **"suite"** is the operator-
> facing grouping of related capabilities — what the L2 PO and the
> operator organize together. **"Module"** (when it appears) is
> reserved for the SA's architectural unit (a service / package /
> deployment boundary). A suite's capabilities may be implemented
> across multiple modules; the SA-modules layer is designed in
> `docs/v2.0/sa-architecture/`. This doc covers L0–L4 only.

## Levels of resolution

| Level | Artifact | What's there | "Done" means |
|---|---|---|---|
| L0 | `.jig/spec/project.md` | Pitch (1 sentence), Problem (1 paragraph), Audience (1 paragraph), product-level Non-goals | Operator confirms; pitch is testable ("does this match what we're building?"). |
| L1 | `.jig/spec/discovery.md` | Personas (1-line each) + Journeys (narrative, per persona) + Capability roster (flat list, traceable to journeys) | Operator declares "all personas covered." No persona has unresolved journeys. |
| L2 | `.jig/spec/suites.yaml` | Ordered list of suites: name, summary, list of L1 capability ids assigned to it | Every L1 capability is in exactly one suite. Suites are 3-5 capabilities each (soft target). |
| L3 | `.jig/spec/suites/<s>/brief.md` | The existing simple-brief format, scoped to one suite's capabilities. | The existing simple-brief "done" — capabilities elaborated, behaviors + AC where state requires them. |
| L4 | Tickets in `.jig/store/tickets.jsonl` | Existing | Existing |

Each level's artifact is ROOM-TO-GROW: incomplete is OK. Operator moves to the next level whenever the current one is
"done enough for now." Resuming work re-reads the artifact and picks up from whatever state is there.

## Artifacts on disk

```
.jig/
  spec/
    project.md                         L0 — pitch / problem / audience / non-goals
    ontology.md                        Project's domain vocabulary (operator's words)
    discovery.md                       L1 — personas / journeys / capability roster
    discovery.state.yaml               L1 — in-flight conversation state (resume support)
    discovery/
      playbacks/
        <journey-id>.md                L1 — Phase-5 playback per committed journey
    suites.yaml                        L2 — suite list
    suites/
      <suite-name>/
        brief.md                       L3 — simple-brief (existing format)
        spec.structured.yaml           L3 — structured projection of brief.md
    project.structured.yaml            top-level federated spec (small; points at suites)
```

The top-level `project.structured.yaml` is a thin federation:

```yaml
spec_version: 2
name: jig-search
summary: hosted search SaaS for ecommerce
suites:
  - id: catalog
    title: Catalog
    summary: ingestion, normalization, delta updates
    spec_uri: project://spec/suites/catalog/spec
  - id: query
    title: Query
    summary: search, autocomplete, faceted results
    spec_uri: project://spec/suites/query/spec
non_goals:
  - id: no-cms
    text: We will not build a CMS
    rationale: out of scope for v2
generated_at: 2026-04-30T18:42:00Z
```

`spec_version: 2` distinguishes from today's monolithic v2 spec.

## L0 — Pitch (mostly existing)

The current `project.md` intro paragraph + the new "Problem statement" section + audience + product-level non-goals. PO
writes this in a short conversation (3-5 turns). Format:

```markdown
# <project name>

<one-sentence pitch>

## Problem

<one paragraph: what's broken in the world this fixes, and why it
matters to the audience>

## Audience

<one paragraph: who uses this and what they're trying to do>

## Non-goals (product-level)

- {#no-cms} We will not build a CMS — out of scope for v2
- ...
```

The L0 PO doesn't ask about features or suites. Just shape.

## L1 — Discovery (new, the load-bearing piece)

### Discovery artifact

```markdown
# <project name> — Discovery

## Personas

- {#customer} The end-user end-user — shoppers hitting the merchant's storefront
- {#merchant} The buyer who integrates jig into their ecommerce site
- {#maintainer} Our team running the system in production

## Journeys

### Merchant onboarding {#j-merchant-onboarding} (persona: merchant)

The merchant first hears about us via SEO or word of mouth, lands on
our marketing site, and self-serves a signup. After creating an
account they receive an API key. Their first concrete action is
uploading their catalog — usually via Shopify API connection (most
common) or CSV upload (fallback). Once the catalog is normalized and
indexed (~5 minutes for typical 5-10k SKU catalogs) they install our
JS snippet on a staging site, test a few queries, and go live.

Capabilities implied:
- {#self-serve-signup} Self-serve account creation with email
- {#api-key-gen} API key generation per account
- {#shopify-connect} Connect Shopify store via OAuth
- {#csv-upload} CSV upload for catalog
- {#index-build} Build searchable index from a catalog
- {#js-snippet} Generate copy-paste JS snippet for site integration

### Shopper search {#j-shopper-search} (persona: customer)
...
```

### Capability roster

A running list at the bottom of `discovery.md`, deduplicated by id, sorted by first-mention. Each capability has a
`journeys:` list of which journey ids surfaced it. This is the input to L2.

### L1 PO behavior

The L1 PO walks the operator through one persona at a time, one journey at a time. Each journey is a five-phase
conversation; the abstract loop primitives (NARRATIVE / EXTRACT / CONFIRM / APPEND / LOOP — same vocabulary used across
PO / SA / VD / PM discovery) get instantiated as five phases below.

**Phase 1 — Frame & find the primary persona** (per project, runs once).

PO doesn't accept "users." It forces naming: which persona is this for, what's their role, why is this their problem to
solve? Then it asks which other personas matter and which is *primary* — so the first journey is unambiguous.

**Phase 2 — Elicit the primary job** (per persona, runs once at start of persona's first journey).

PO rejects feature-shaped answers ("a dashboard with charts") and pushes for outcome ("I know who's blocked before my
1:1s"). Goal is to get one job right rather than three jobs vaguely. This anchors the rest of the journey work for this
persona.

**Phase 3 — Walk the journey, step by step** (per journey, the bulk of the work).

PO walks one step at a time. After each operator answer, PO reflects the *implication* back, not just the fact ("so the
entry point is Slack" rather than "got it, Slack"). Each reflection surfaces an assumption the operator can correct
before the next step builds on it. PO probes: "what triggers her to open the app?" → "what does she see?" → "what does
she do next?" Linear chronology; one branch at a time.

**Phase 4 — Probe failure modes at each step** (per step, in-line with Phase 3).

After each step, PO asks "what could go wrong, what's missing, what's ambiguous." This is where most real requirements
live. Examples on a summary screen: "what if nobody posted yet? what if three people are blocked on the same thing? what
if someone's blocker is 'I'm sick today'?"

**Phase 5 — Stop, play back, commit** (per journey, the artifact-producing turn).

PO doesn't open new threads until the current one is closed. At the end of each journey it reads the journey back in the
operator's own language, structured as a numbered list, and asks "what's wrong" (not "is this right" — the former
invites correction; the latter invites rubber-stamp). When the operator confirms or corrects, PO commits the journey to
`discovery.md` along with its extracted capabilities. **This is the artifact-producing moment.**

After Phase 5, PO loops: "Another journey for this persona, or move on?" When all journeys for one persona are walked,
PO confirms persona-complete and proposes the next persona (suggested defaults: customer, merchant, maintainer — adjust
per pitch). When operator declares L1 done, PO calls `discovery_finalize` which validates:

- Every persona has at least one journey
- Every journey extracts at least one capability
- All capability ids are kebab-case and unique
- No two journeys have the same id

### L1 PO mechanics — what makes the conversation work

These are non-negotiable patterns the prompt enforces. They distinguish a useful discovery conversation from a "helpful
AI" conversation that misses the point:

- **One question per turn.** Two questions in one turn = the operator answers one and ignores the other. Even if the
  second question is obvious, it gets dropped.
- **Reflect implications, not facts.** "So the app's job on that path is making 'looks off' obvious in the summary and
  making the click-through fast" — that surfaces an assumption. "Got it, Slack ping" doesn't.
- **Refuse feature-shaped answers.** When operator says "a dashboard with charts," PO redirects: "what outcome does the
  dashboard exist to serve? if the answer is 'see things at a glance,' what specifically should they see and why?"
- **Track `current_persona` and `current_step` explicitly.** Probing branches don't lose the trunk. If Phase 4 surfaces
  a failure-mode tangent, PO returns to Phase 3 step N+1 once the tangent is resolved.
- **Playback before progression.** No persona moves to "done" without a per-journey playback. No L1 moves to finalize
  without a per-persona playback summary.
- **Operator's vocabulary.** Capability ids and journey narratives use the operator's language, not invented
  terminology. "Spot blockers early" not "early-warning intervention surface."
- **Capture domain terms as they come up.** When the operator uses a domain word that hasn't been seen before
  ("blocker," "standup," "looks off"), PO captures it for the **project ontology** — see the section below. Don't break
  flow to confirm; capture pending and surface during Phase-5 playback.
- **Concrete over abstract.** "9am Slack summary lands; she scans for 'looks off' signals" beats "morning notification
  triggers attention." The concrete version is what the spec ends up using.

### Project ontology — capturing the operator's domain vocabulary

Every project has a `.jig/spec/ontology.md` file capturing the operator's domain vocabulary as it emerges during PO
discovery. This is the project's *ubiquitous language* — the words people who actually use the product would use, not
words jig or the agent invented. Every subsequent agent (PO continuing discovery, SA, VD, PM, dev) reads this so
terminology stays consistent across the project's artifacts and code.

**Why it exists:** the "operator's vocabulary" mechanic above is only sustainable if there's a single place that
captures the vocabulary. Without it, each agent re-derives terms from journey narratives and drifts independently. With
it, "blocker" is "blocker" everywhere — in the brief, in contracts, in code, in tests, in error messages.

**Format:**

```markdown
# <project-name> — Domain Vocabulary

The terms used by people who actually use this product. The operator's words, not jig's words. Every agent reads
this for consistent terminology. Operator can edit directly.

## Terms

### blocker
Something preventing a team member from making progress. Surfaced in async standup posts. Can be a person ("waiting
on Sarah"), a system ("CI is down"), or a question ("what should the API return when X").
First mentioned in: j-merchant-tuesday-morning.

### standup
The daily-or-weekly ritual where team members report status. In this product specifically: async, posted into Slack
(or elsewhere), summarized for the manager.
First mentioned in: j-merchant-tuesday-morning.

### "looks off" signals
The visual indicators on the summary screen that something needs attention. Operator's vocabulary placeholder —
specific signals not yet defined (some combination of late posts, blocker count, repeat blockers, sentiment).
First mentioned in: j-merchant-tuesday-morning.
```

**How it gets populated:**

During Phase 3 / Phase 4 of the journey walk, the PO recognizes when the operator uses a domain term. PO doesn't break
the conversation flow to ask about every term — that would be tedious and break the implication-reflection rhythm.
Instead PO uses `ontology_stash_term` to capture the term as pending. At Phase 5 playback, before committing the
journey, PO surfaces the pending terms with proposed definitions:

> **PO:** Before I commit this journey, a few terms came up I want to make sure I have right:
>
> - **"blocker"** — I'm reading this as "anything preventing a team member from making progress, posted in their
>   standup." Right? Anything to add?
> - **"looks off"** — I think this is the visual signal on the summary screen that flags something needing
>   attention. We haven't pinned down what specifically; want to leave it as a placeholder for now?

Operator confirms / refines, PO commits via `ontology_add_term`, then commits the journey itself.

**How other agents use it:**

- **L2 PO** reads ontology when proposing suite names — uses domain terms where applicable.
- **SA** reads ontology when authoring contracts; data shape field names should derive from domain vocabulary
  ("blocker_id" not "issue_id").
- **VD** reads ontology when authoring wireframes; labels and component names use domain terms ("Blocker" component not
  "Issue" component).
- **Dev agents** read ontology as part of their always-injected context (cheap, high cost-of-miss). Variable names,
  function names, error messages, and user-facing strings use domain vocabulary.
- **Reviewer agents** can flag implementations that introduce synonyms not in the ontology — e.g., code calls them
  "obstacles" while the ontology says "blockers."

**Operator-edit-friendly:** the ontology is plain markdown with a fixed structure. Operator can edit directly to refine
definitions, merge synonyms, or add terms PO missed. PO re-reads on resume so operator edits land cleanly.

### L1 PO conversation — worked example

A walkthrough of the manager persona for a hypothetical "tool that helps small teams run async standups." Shows the five
phases in concrete dialogue. Use as the canonical reference for what the conversation should look like.

**Phase 1 — Frame & find the primary persona:**

> **PO:** Let's start by pinning down who this is for. When you picture someone opening this app, who is it — what's
> their role on the team, and why is async standup their problem to solve?
>
> *operator: engineering manager of a 6-person remote team*
>
> **PO:** Got it. Are there other people whose experience matters here — the team members posting updates, or only
> the manager reading them? I want to know which personas we'll need journeys for, and which is primary.

**Phase 2 — Elicit the primary job:**

> **PO:** For the manager — forget features for a second. What outcome does she walk away with on a good day? "I
> posted standup" isn't an outcome; "I know who's blocked before my 1:1s" might be.
>
> *operator: she wants to spot blockers before they fester, without sitting in a 30-min call*
>
> **PO:** Good. Anything else she's getting from this, or is "spot blockers early" the load-bearing one? I'd rather
> get one job right than three jobs vaguely.

**Phase 3 — Walk the journey, step by step:**

> **PO:** Okay, let's walk through a Tuesday morning. What triggers her to open the app — is it a notification, a
> calendar block, a habit?
>
> *operator: she gets a Slack ping at 9am summarizing overnight posts*
>
> **PO:** So the entry point is actually Slack, not the app. That's important. Does she click through, or does the
> Slack message itself contain enough that she rarely opens the app?
>
> *operator: she clicks through if something looks off*
>
> **PO:** Good — so the app's job on that path is "make 'looks off' obvious in the summary, and make the
> click-through fast to act on." Let's keep going. She clicks through. What does she see, and what does she do next?

**Phase 4 — Probe failure modes at each step:**

> **PO:** At the summary screen — what happens if nobody posted yet? What if three people are blocked on the same
> thing? What if someone's blocker is "I'm sick today"? I want to know the shape of the messy cases, not just the
> happy path.

**Phase 5 — Stop, play back, commit:**

> **PO:** I think I have enough for the manager's primary journey. Before we move to the team-member persona, let
> me play it back — tell me what's wrong:
>
> 1. 9am Slack summary lands; she scans for "looks off" signals
> 2. Clicks through only if something needs action
> 3. On the blocker view, she can either DM the person or mark it for her 1:1 list
> 4. Done — she closes the tab and gets on with her day
>
> Is that the journey, or am I missing a branch?

After operator confirms (or corrects and re-confirms), PO calls `discovery_add_journey` with the structured narrative,
then `discovery_add_capability` for each implied capability (using verbs the operator actually said — "scan summary,"
"click through to blocker," "DM person," "mark for 1:1"). The journey is now committed; PO moves to the next journey or
persona.

Then **Phases 2–5 repeat for each remaining persona.** At the end of L1, PO synthesizes: shared steps across journeys →
shared capabilities (already deduped via `discovery_add_capability`'s merge-on-id behavior); divergent steps →
persona-specific capabilities (each only cited by one persona's journey).

### L1 conversation state and resume

L1 conversations span 30-50 turns for a medium project. They will not always finish in one sitting. Three mechanisms
keep state across operator pauses, daemon restarts, and explicit resume:

**1. The append-only `discovery.md`** is the cumulative output — every committed journey + capability roster update
lands here. Already designed. Resume re-reads this to know what's been finalized.

**2. A new `discovery.state.yaml`** tracks the in-flight conversation:

```yaml
# .jig/spec/discovery.state.yaml
spec_version: 1
status: in_progress             # in_progress | finalized
current_persona: merchant
current_journey: j-merchant-onboarding
current_phase: 3                # 1-5 per the phase numbering
current_step: 4                 # within Phase 3, the step index
phases_completed_this_journey: [1, 2]
journeys_completed_this_persona: []
personas_completed: [customer]
personas_pending: [merchant, maintainer]

# Pause point: what the PO was about to ask, captured so resume
# doesn't re-derive (and possibly drift)
next_question: |
  At the summary screen, what happens if nobody posted yet?
  Three blockers on the same thing? Someone says "I'm sick today"?

# In-flight scratch — capabilities the PO has tentatively extracted
# from this journey but not yet committed via discovery_add_capability
pending_capabilities:
  - id: scan-overnight-summary
    description: "Scan summary for 'looks off' signals"
    journey: j-merchant-tuesday
  ...
```

The state file is rewritten after every meaningful operator turn (not on every PO turn — the PO can recompute its intent
if dropped mid-thought, but the operator's input must not be lost).

**3. A `playback.md` per journey**, captured at the moment Phase 5 fires:

```
.jig/spec/discovery/playbacks/<journey-id>.md
```

The numbered playback that was shown to the operator, with the operator's confirmation or correction recorded. This is
the audit trail of what was actually committed to discovery.md, in the operator's own words. Useful for later layers
(SA, VD) that want to know "what did the operator originally agree to" not just "what's the final extracted artifact."

On resume:

1. PO reads `discovery.md` (committed history) + `discovery.state.yaml` (in-flight position) + the latest `playback.md`
   if any.
2. PO greets: "Picking up where we left off — you were in the middle of the manager's Tuesday-morning journey. We
   established the Slack-ping entry point and that she clicks through when something looks off. Next I was about to ask:
   at the summary screen, what happens if nobody posted yet?"
3. Operator either continues or redirects.

The state primitive (current_persona / current_step / playback / next_question) is general — same shape applies to SA
discovery (current_module / current_contract_step / playback) and VD discovery (current_screen). A shared implementation
is plausible.

### L1 PO tools (new MCP tools)

Two groups: artifact-mutation tools (write to disk) and state-tracking tools (update conversation position).

**Artifact-mutation tools — write to `discovery.md` and friends:**

```
discovery_set_intro(pitch, problem, audience)        — write L0 sections to project.md
discovery_add_persona(id, description)               — append to Personas section
discovery_list_personas()                            — read Personas section
discovery_add_journey(persona_id, journey_id,
                      narrative, playback_text)      — append a journey block; capture
                                                       Phase-5 playback to discovery/playbacks/
discovery_add_capability(capability_id, description,
                         journey_ids)                — append to roster (merge if id exists)
discovery_get_state()                                — read full discovery.md (committed)
discovery_finalize()                                 — validate + emit event for L2 PO
ask_question(ticket_id="discovery", questions=[...]) — same as today
```

**State-tracking tools — update `discovery.state.yaml`:**

```
discovery_set_phase(persona_id, journey_id,
                    phase, step)                     — record current_persona, journey,
                                                       phase (1-5), step within phase
discovery_set_next_question(question_text)           — capture the question PO is about
                                                       to ask, so resume works mid-turn
discovery_stash_pending_capability(id, description,
                                    journey_id)      — tentative capability before
                                                       Phase-5 commit
discovery_clear_pending(journey_id)                  — clear stash after journey commits
discovery_load_state()                               — read discovery.state.yaml on resume
```

**Project ontology tools — update `.jig/spec/ontology.md`:**

```
ontology_stash_term(term, tentative_definition,
                    first_seen_in_journey)           — capture during journey walk;
                                                       confirmed at Phase-5 playback
ontology_add_term(term, definition,
                  first_seen_in_journey)             — commit a term after operator
                                                       confirms at playback
ontology_get_terms()                                 — read full project ontology
ontology_lookup(term)                                — single-term definition lookup
```

Brief format rules for `discovery.md`:
- Persona ids are kebab-case (`{#merchant}`)
- Journey ids start with `j-`, contain persona keyword
- Capability ids are kebab-case
- Every journey lists implied capabilities by id
- Every capability in the roster lists which journeys cite it
- Operator's vocabulary, not invented terminology

### L1 PO prompt (sketch)

```yaml
role: discovery-po
model: claude-sonnet-4-6              # senior tier; conversational reasoning is core
phase_prompt: >
  You are the discovery agent for `jig init`. The operator has already
  pitched the product (project.md exists with pitch + problem +
  audience). Your job is to uncover what the product actually does by
  walking through user journeys, one persona at a time, in disciplined
  five-phase conversations.


  ## What you're producing

  - `discovery.md` — committed personas, journeys, capability roster.
    Append-only. Every commit goes through a Phase-5 playback the
    operator confirmed.
  - `discovery.state.yaml` — your current position in the conversation
    (current persona, journey, phase, step, next question, pending
    capabilities). Updated after every operator turn so you can resume
    if interrupted.
  - `discovery/playbacks/<journey-id>.md` — per-journey audit trail of
    the playback you read back to the operator and what they confirmed.


  ## The five-phase pattern (per journey)

  Phase 1 — Frame & find the primary persona (per project, runs once):
    Don't accept "users." Force naming — role + why this is their
    problem. Ask which other personas matter and which is primary.

  Phase 2 — Elicit the primary job (per persona, at start of their
  first journey):
    Reject feature-shaped answers ("a dashboard with charts"). Push
    for outcome ("I know who's blocked before my 1:1s"). Anchor:
    "I'd rather get one job right than three jobs vaguely."

  Phase 3 — Walk the journey, step by step:
    One step at a time. After each operator answer, reflect the
    *implication* back, not just the fact. "So the entry point is
    Slack — that's important" not "got it, Slack."

  Phase 4 — Probe failure modes at each step (in-line with Phase 3):
    After each step ask "what could go wrong / be missing /
    be ambiguous." Most real requirements live here.

  Phase 5 — Stop, play back, commit (artifact-producing turn):
    Read the journey back as a numbered list, in the operator's
    own language. Ask "what's wrong" not "is this right" — invites
    correction, not rubber-stamp. On confirmation, call
    `discovery_add_journey` + `discovery_add_capability` per
    extracted capability.


  ## Mechanics — non-negotiable

  - **One question per turn.** Two questions = the operator answers
    one and ignores the other.
  - **Reflect implications, not facts.** Every operator answer gets
    a one-sentence reflection that surfaces the assumption you're
    about to build on. The operator can correct before the next
    step locks it in.
  - **Refuse feature-shaped answers.** If operator says "a dashboard
    with charts," redirect: "what outcome does the dashboard exist
    to serve? what specifically should they see, and why?"
  - **Track state explicitly.** Call `discovery_set_phase` after
    every transition (step → step, phase → phase). Call
    `discovery_set_next_question` before asking each question —
    so resume can pick up the exact thread.
  - **Stash pending capabilities.** As you extract them during
    Phase 3-4, call `discovery_stash_pending_capability` so they
    survive interruption. Commit them via
    `discovery_add_capability` at Phase 5 only after operator
    confirms the playback.
  - **Playback before progression.** No journey commits without
    Phase 5. No persona moves to "done" without confirming with
    the operator that all the persona's journeys are walked. No
    L1 finalizes without all personas confirmed complete.
  - **Operator's vocabulary always.** Capability ids derive from
    verbs the operator actually used. Journey narratives use the
    operator's words, not your reformulations.
  - **Capture domain terms to the project ontology as you go.** When
    the operator uses a domain noun ("blocker," "standup," "looks
    off"), call `ontology_stash_term` with a tentative definition.
    Don't break flow to confirm. At Phase 5, surface stashed terms
    alongside the journey playback for operator confirmation, then
    commit via `ontology_add_term`.
  - **Concrete over abstract.** "9am Slack summary lands; she scans
    for 'looks off' signals" beats "morning notification triggers
    attention."


  ## Resume behavior

  At session start, if `discovery.state.yaml` exists with status
  `in_progress`:
  1. Call `discovery_load_state` and `discovery_get_state`.
  2. Read the latest `playback.md` if any committed journey exists.
  3. Greet with what's in flight: "Picking up where we left off — you
     were in the middle of <persona>'s <journey-id>. We established
     <reflection of last committed step>. Next I was about to ask:
     <next_question from state>. Continue, or want to change course?"
  4. Operator either continues or redirects. Either way, update state
     before asking the next question.


  ## Suggested persona defaults

  Most projects have some shape of customer / merchant / maintainer.
  Adapt to the pitch — for an internal tool the personas may be
  different roles; for a B2C product "merchant" may not apply.
  Always confirm with the operator; never assume.


  ## Done criteria

  Call `discovery_finalize` only when:
  - Every persona has at least one walked-and-committed journey.
  - Every committed journey has at least one extracted capability.
  - All capability ids are kebab-case and unique.
  - Operator has explicitly said "done for now" or equivalent.

  See ./design.md "L1 PO conversation — worked example" for the
  canonical reference walkthrough.

allowed_tools:
  - discovery_set_intro
  - discovery_add_persona
  - discovery_list_personas
  - discovery_add_journey
  - discovery_add_capability
  - discovery_get_state
  - discovery_finalize
  - discovery_set_phase
  - discovery_set_next_question
  - discovery_stash_pending_capability
  - discovery_clear_pending
  - discovery_load_state
  - ontology_stash_term
  - ontology_add_term
  - ontology_get_terms
  - ontology_lookup
  - ask_question

strict_tools: true
```

## L2 — Suite organization (new)

### suites.yaml

```yaml
spec_version: 2
suites:
  - id: onboarding
    title: Onboarding
    summary: signup, API key, JS snippet, billing
    capabilities:
      - self-serve-signup
      - api-key-gen
      - js-snippet
      - billing
    status: pending     # pending | brief_ready | resolved
  - id: catalog
    title: Catalog
    summary: ingestion, normalization, delta updates
    capabilities:
      - shopify-connect
      - csv-upload
      - normalize-skus
      - delta-update
    status: pending
  ...
crosscutting_non_goals:
  - {#no-cms} ...
```

Every L1 capability appears in exactly one suite's `capabilities` list. Validation: union of all suite capabilities ==
discovery roster.

### L2 PO behavior

Reads `discovery.md`. Proposes 4-6 suite groupings with one-line rationale each. Operator confirms / rearranges. PO
writes `suites.yaml`.

If the operator's product genuinely has fewer/more suites, PO proposes that — soft target of 3-5 capabilities per suite
is a heuristic, not a rule.

### L2 PO tools

```
discovery_get_capability_roster()           — read flat list from L1
suites_propose(suites: list)                — show operator proposed grouping; await confirmation
suites_finalize(suites: list)               — write suites.yaml
ask_question(ticket_id="suites", ...)
```

## L3 — Suite brief (existing, scoped to one suite)

`/suite init <name>` (TUI slash command) runs the existing simple-brief PO with two adjustments:

1. The PO sees the parent context: project.md (L0) + discovery.md (L1)
   + suites.yaml entry for this suite. So the PO knows which
capabilities are in scope and what role this suite plays.
2. The brief at `.jig/spec/suites/<s>/brief.md` only enumerates the capabilities listed in `suites.yaml` for that suite
   — adding new capabilities here flags a gap (operator must go back to L1).

Spec-gen / SA / scaffold proceed as today, but produce `.jig/spec/suites/<s>/spec.structured.yaml` instead of the
project- level structured spec.

## L4 — Tickets (existing)

Tickets get a `suite_id` field so they're scoped. `derived_from: project://spec/suites/<s>/capabilities/<c>`. The
orchestrator already supports this URI shape via the work in `jig/spec_uri.py`.

(Tickets may also gain a `module_id` field once the SA-modules layer lands — `suite_id` is the *what* and `module_id` is
the *where*. Out of scope for this design.)

## Iteration: adding a journey later

`/journey add <persona>` is a top-level operation:

1. L1 PO is spawned with the persona id + existing discovery.md.
2. Walks the new journey, extracts capabilities.
3. For each new capability, checks: is it already in suites.yaml?
   - If yes, no-op (just adds journey ref to existing capability).
   - If no, proposes either appending to an existing suite (with rationale) OR creating a new suite.
4. Operator confirms; L1 + L2 artifacts updated.
5. If the new capability lands in a suite whose `brief.md` already exists, the brief is flagged as stale; operator runs
   `/suite refresh <s>` to incorporate.

## Workflow integration

> **CLI vs TUI surface.** Jig is TUI-first. The CLI surface stays minimal: `jig` (launch TUI), `jig daemon
> start|stop|status` (daemon lifecycle), `jig build`, `jig story <ticket-id>`, and `jig --print "/<command>"` (one-shot
> escape hatch for scripts). All workflow operations — suite, journey, plan, etc. — are TUI slash commands. The
> `--print` escape hatch makes them scriptable when needed without duplicating the surface.

`jig create <name>` continues to launch the TUI. Inside:

```
/init                  → drives L0, L1, L2 in sequence, with explicit
                          checkpoints between levels (operator must
                          confirm at each transition)
/init --resume         → resumes wherever the artifacts say we are
/suite list            → shows suites.yaml status (pending vs done)
/suite init <name>     → L3 simple-brief for that suite
/suite refresh <name>  → re-run suite brief incorporating L1 changes
/journey add <persona> → L1 add-journey iteration
/journey list          → show personas + journeys
```

The existing `/init <name>` (with project name argument) creates a new project AND runs L0+L1+L2. The existing `/init`
(no args) inits the cwd. Both default to running through L0/L1/L2 — the operator can stop early by replying "I'm done
for now" to the PO.

## Federated spec generation

`spec-generator` becomes mode-aware:
- `spec-generator --level project` reads suites.yaml + crosscutting non-goals from project.md, produces
  project.structured.yaml.
- `spec-generator --level suite --suite <id>` reads suites/<id>/brief.md, produces suites/<id>/spec.structured.yaml.

`/spec` slash commands gain suite scope:
- `/spec capabilities --suite catalog` lists capabilities in one suite
- `/spec capability catalog/normalize-skus` reads one capability's full spec

URI scheme:
- `project://spec/suites/<s>/capabilities/<c>` — capability in a suite
- `project://spec/suites/<s>` — whole suite spec
- `project://spec/suites` — list of suites
- `project://spec/discovery/journeys/<j>` — read a discovery journey
- `project://spec/discovery/personas/<p>` — read a persona

## Risks

- **PO conversation length at L1.** Walking 3 personas × 2-3 journeys could be 30-50 turns. Token costs add up.
  Mitigation: each persona is its own PO spawn — operator can break across sessions; PO reads discovery.md state on
  resume.
- **Capability id collisions / drift.** L1 generates ids; L3 might want different names. Mitigation: id is the source of
  truth; L3 cannot rename without going back to L1.
- **Operator gets stuck mid-journey.** Hard to know what to say next. Mitigation: PO can offer 2-3 "what often comes
  next" suggestions drawn from common ecommerce/SaaS patterns. (Risk: prejudicing the design. Watch for this.)
- **L2 grouping is ambiguous.** Operator might disagree with PO's proposal. Mitigation: PO proposes; operator owns the
  final shape. Multiple iterations supported.
- **Existing simple-brief workflow.** Backward-compat: a project that has `.jig/spec/project.md` in old format (with `##
  Built` etc. and enumerated capabilities) keeps working — there's just no L1/L2 artifacts. New projects use the
  multi-level path. Detection: presence of `discovery.md`.

## Out of scope

- Cross-suite dependency graph (capability X in suite A depends on capability Y in suite B). Operator-tracked for now.
- Time-horizons / versioning (v2 capabilities vs v2). Suites are flat; add a `target_version` field later if needed.
- Multi-team ownership. Each suite gets one assignee at most.
- Migration from any prior jig format. Confirmed 2026-05-03: clean break, no migration tooling, no backward-compat
  detection. v2 is the format from day one.
- The SA-modules layer (architectural code modules, contracts, integration AC, risk register, spike work). See
  `docs/v2.0/sa-architecture/`.

## Open questions

1. **L1 PO suggestions for "what comes next" mid-journey** — should it propose, or stay strictly Socratic? Current
   design: stays Socratic by default, but operator can `/journey suggest` to opt in to suggestions for one turn.
2. **suites.yaml status field** — should `status` track L3 progress (pending / brief_ready / resolved), or is that
   derivable from disk (does `suites/<s>/brief.md` exist + is the ticket resolved)? Probably derivable — drop the field.
3. **Crosscutting concerns**: where to express auth / multi-tenancy / observability. Three options from earlier
   brainstorm — locked in: **dedicated Platform suite** (a maintainer-journey-driven suite), plus product-level
   non-goals for things we won't build at all.
4. **L1 → L2 transition trigger** — does L1 PO automatically hand off to L2 PO on `discovery_finalize`, or does the
   operator type `/suites organize` separately? Current design: auto-handoff with an "approve to proceed" gate (similar
   to current brief-approval).

## Implementation phases

1. **Schema** — `discovery.md` parser, `suites.yaml` schema + validators, `project.structured.yaml` v2 schema. New URI
   shapes in `spec_uri.py`.
2. **L0 PO** — split the existing init PO into pitch-only + journey- discovery. The pitch-only is mostly the existing
   intro flow; just shorten its prompt.
3. **L1 PO + tools** — new role, new MCP tools, new brief parser for journey blocks.
4. **L2 PO + tools** — simpler than L1; reads roster, proposes grouping.
5. **Daemon command handlers** for `suite_init`, `suite_list`, `suite_refresh`, `journey_add`, `journey_list` — wired
   into the existing daemon command registry so they're invokable as TUI slash commands and (via `jig --print
   "/<command>"`) from the shell for scripting.
6. **TUI affordances** — `/journey add`, `/suite list`, etc., plus operator-facing rendering (suite status, journey
   list, gate confirmations).

## Change log

- 2026-04-30: Initial draft (brent + claude). Captures the five-level design, L1 PO sketch, federated structured spec,
  iteration story.
- 2026-04-30: Renamed L2 concept "module" → "suite" to free up "module" for the SA's architectural unit. No structural
  changes.
- 2026-05-03: Major expansion of the L1 PO section. Replaced thin five-step loop description with a five-phase
  conversation pattern (Frame & primary persona / Elicit primary job / Walk journey step-by-step / Probe failure modes /
  Stop, play back, commit). Added "L1 PO mechanics" section enumerating the non-negotiable conversation patterns (one
  question per turn, reflect implications not facts, refuse feature-shaped answers, track state explicitly, playback
  before progression, operator's vocabulary, concrete over abstract). Added "L1 PO conversation — worked example" with
  full dialogue walkthrough on a hypothetical async-standup-tool project. Added "L1 conversation state and resume"
  section: new `discovery.state.yaml` for in-flight position + per-journey `playbacks/<journey-id>.md` for audit trail;
  resume behavior specified. Expanded MCP tool list with state-tracking primitives (`discovery_set_phase`,
  `discovery_set_next_question`, `discovery_stash_pending_capability`, `discovery_clear_pending`,
  `discovery_load_state`). Rewrote prompt sketch with phase-by-phase guidance, mechanics section, and resume behavior.
  Worth noting: state primitive (current_persona / current_step / playback / next_question) is a general pattern that
  should apply to SA discovery (current_module / current_contract_step), VD discovery (current_screen), and PM planning
  (current_capability / current_ticket).
- 2026-05-03: Added project ontology — a per-project `.jig/spec/ontology.md` capturing the operator's domain vocabulary
  as it emerges during PO discovery. Distinct from jig's own `ontology.md` (jig's vocabulary). Captured by L1 PO via
  `ontology_stash_term` during journey walks, confirmed at Phase-5 playback alongside the journey, committed via
  `ontology_add_term`. Read by every subsequent agent (PO continuing, SA, VD, PM, dev) so terminology stays consistent
  across the project's artifacts and code. New "Project ontology" section in this design; ubiquitous-language tenet
  added to TENETS.md tenet 4; "Project ontology" + "Domain term" terms added to jig's ontology.md. Inspired by DDD's
  ubiquitous-language discipline without inheriting the rest of the DDD jargon.
