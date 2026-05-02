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
> `docs/sa-architecture/`. This doc covers L0–L4 only.

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
    discovery.md                       L1 — personas / journeys / capability roster
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
    rationale: out of scope for v1
generated_at: 2026-04-30T18:42:00Z
```

`spec_version: 2` distinguishes from today's monolithic v1 spec.

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

- {#no-cms} We will not build a CMS — out of scope for v1
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

The L1 PO walks the operator through one persona at a time, one journey at a time. Pattern per journey:

1. **NARRATIVE** — open-ended question: "When does <persona> first show up? What do they do, then what?" Iterates until
   the journey reaches a natural endpoint or operator says "stop."
2. **EXTRACT** — PO drafts a list of capabilities the journey implies (using only verbs from the operator's narrative,
   no inventing).
3. **CONFIRM** — shows operator the journey + capabilities. Operator edits names, drops some, adds missing ones.
4. **APPEND** — PO writes the journey + capability deltas to `discovery.md`.
5. **LOOP** — "Another journey for <persona>, or move on?"

When all journeys are walked for one persona, PO confirms persona is done and proposes the next persona (suggested
defaults: customer, merchant, maintainer; operator can reorder/rename/add). When operator declares L1 done, PO calls
`discovery_finalize` which validates:
- Every persona has at least one journey
- Every journey extracts at least one capability
- All capability ids are kebab-case and unique

### L1 PO tools (new MCP tools)

```
discovery_set_intro(pitch, problem, audience)        — write L0 sections to project.md
discovery_add_persona(id, description)               — append to Personas section
discovery_list_personas()                            — read Personas section
discovery_add_journey(persona_id, journey_id, narrative)
                                                     — append a journey block
discovery_add_capability(capability_id, description, journey_ids)
                                                     — append to roster (or merge if id exists)
discovery_get_state()                                — read full discovery.md
discovery_finalize()                                 — validate + emit event for L2 PO
ask_question(ticket_id="discovery", questions=[...]) — same as today
```

Brief format rules for `discovery.md`:
- Persona ids are kebab-case (`{#merchant}`)
- Journey ids start with `j-`, contain persona keyword
- Capability ids are kebab-case
- Every journey lists implied capabilities by id
- Every capability in the roster lists which journeys cite it

### L1 PO prompt (sketch)

```yaml
role: discovery-po
phase_prompt: >
  You are the discovery agent for `jig init`. The operator has already
  pitched the product (project.md exists with pitch + problem +
  audience). Your job is to uncover what the product actually does by
  walking through user journeys, one persona at a time.


  ## What you're producing

  A `discovery.md` document with:
  - Personas section (1-line each)
  - Journeys (one block per journey, narrative + extracted capabilities)
  - Capability roster (flat list of capability ids → which journeys
    surfaced them)


  ## How to work

  1. Suggest the standard personas — customer, merchant, maintainer —
     and ask which apply + what others the operator wants to add.

  2. For each persona, walk one journey at a time using the pattern:
     ```
     NARRATIVE  ask "what happens?"; gather chronological steps until
                the journey reaches a natural endpoint or the operator
                says "stop"
     EXTRACT    propose 3-8 capabilities the narrative implies — use
                only verbs the operator already said; don't invent
     CONFIRM    show the journey + capabilities; ask the operator to
                edit / drop / add
     APPEND     write the journey + capability deltas
     LOOP       "another journey for this persona, or move on?"
     ```

  3. When all journeys are done for a persona, confirm the persona is
     complete and move to the next.

  4. End the conversation by calling `discovery_finalize` once all
     suggested personas have been declined or covered.


  ## Constraints

  - Capability ids are kebab-case. Names use the operator's vocabulary,
    not invented terminology.
  - "No functionality outside a journey." If you find yourself wanting
    to add a capability that doesn't trace to a journey, ask the
    operator: "what journey is this for?" If they can't answer, drop it.
  - Don't enumerate AC, behaviors, or anything that belongs at L3. The
    capability roster is one-line-per-capability max.

  - Prefer concrete to abstract. "Upload catalog as CSV" beats
    "Catalog management."

allowed_tools:
  - discovery_set_intro
  - discovery_add_persona
  - discovery_list_personas
  - discovery_add_journey
  - discovery_add_capability
  - discovery_get_state
  - discovery_finalize
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
- Time-horizons / versioning (v1 capabilities vs v2). Suites are flat; add a `target_version` field later if needed.
- Multi-team ownership. Each suite gets one assignee at most.
- Migration of existing projects from monolithic brief to multi-level. Manual for now.
- The SA-modules layer (architectural code modules, contracts, integration AC, risk register, spike work). See
  `docs/sa-architecture/`.

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
7. **Migration** — backwards-compat detection (old vs new project).

## Change log

- 2026-04-30: Initial draft (brent + claude). Captures the five-level design, L1 PO sketch, federated structured spec,
  iteration story.
- 2026-04-30: Renamed L2 concept "module" → "suite" to free up "module" for the SA's architectural unit. No structural
  changes.
