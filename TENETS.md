# Jig Tenets

Aspirational principles, not contracts. Similar to the zen of Python, they are tenets we all agree on that guide our
work.

---

## Why Jig Exists

Jig seeks to achieve two things:

1. Make agents better at what they already do well

Coding agents are good at small, well-scoped tasks and bad at big ones. Left to their own devices on a medium-or-larger
project, an agent will produce a plausible-looking pile of code that doesn't actually do what the app is supposed to do.
Jig's job is to keep the agent on the small-task side of that line, while still ending up with software where the pieces
fit together. **Correctness — does the app actually do what it's supposed to do — is the bar.** Everything below is in
the service of that.

2. Make Humans better at what they do well
Projects often require more thought than most people think. The second goal is to make humans better at what they do
well, which is thinking deeply and driving the project. Jig applies a layer of thinking discipline that smaller projects
don't usually have. Humans understand the "why" of the project. They see how it helps people in the real world. They
understand the tradeoffs in the decisions they make.



---
Consult ontology.md for a list of terms used throughout the project and their Jig-specific meanings.
---

## 1. Bite-sized work, coherent whole

Agents do their best work on tasks small enough to hold in one window of attention — a single function, a single
behavior, a single ticket. They do their worst work on tasks large enough that they have to make architectural calls
mid-implementation. So jig's first job is to **keep the agent's task small, and jig's second job is to make sure the
small tasks add up to a coherent system.**

The first half is mostly tractable: levels of resolution (L0 → L4), per-suite briefs, per-ticket scope, strict tool
surfaces, AC that fence one behavior at a time. We have real tools for this.

The second half is the open problem. Decomposition is easy; coherence under decomposition is hard. The pieces have to
share vocabulary, agree on contracts, and not silently disagree about what the system does. Today we lean on:

- A single source of truth at each level (one brief per suite, one structured spec per project) so two agents can't be
  working from divergent versions of "the truth."
- Stable ids and a URI scheme so an agent in one ticket can name a thing defined in another and mean exactly the same
  thing.
- The PO / SA / spec-gen handoffs as integration points where divergence between bite-sized pieces gets caught before
  code is written.

But coherence is not solved. It's the thing to keep designing toward.

Implications:

- Always prefer breaking a task down further over giving an agent more context to "handle" a bigger task.
- A workflow phase that exists to enforce coherence (brief approval, SA confirm, scaffold confirm) is load-bearing —
  don't treat those gates as friction.
- When something is hard to decompose, that's a design smell at the spec level, not just at the code level. Push back
  up.

Anti-patterns:

- "The agent can probably handle this whole suite" — no, give it one capability at a time.
- Treating coherence as something that emerges for free if every ticket passes its own tests.
- Letting two pieces of the system describe the same concept in different words and assuming the agent will reconcile
  them.

---

## 2. Exact context, no more

Agents perform best when given **exactly the context they need to do their task — and no more.** Extra context dilutes
attention, increases hallucination risk, and makes it likely the agent solves a different problem than the one in front
of it.

Implications:

- Roles have **strict tool surfaces** (`strict_tools: true`) — they can only call what their job requires.
- Briefs are **scoped** — a dev agent on a catalog ticket reads catalog's suite brief, not the whole product spec.
- Suites exist as a context-scoping primitive at the spec level; modules (the SA's architectural units) are a separate
  concern. (See `docs/v2.0/multi-level-spec/design.md`.)
- The structured spec is **federated** — looking up a single capability doesn't drag in the whole project.
- Decision records, comments, learnings get pulled in **on demand** via URIs, not flooded into the prompt.

Anti-patterns:

- "Just give the agent the whole project, it'll figure out what's relevant" — it won't, and even if it does, it'll cost
  10× the tokens and degrade the answer.
- Adding a context block "just in case."
- Making one big prompt that covers every situation; prefer phase- / role-specific prompts.

---

## 3. Clear instructions and boundaries

Agents perform best when given **clear instructions and explicit boundaries.** Ambiguity in either direction (vague
task, vague scope) produces ambiguous output — and on a long-running project the ambiguity compounds.

Implications:

- Every role has a phase-specific prompt that says exactly what the agent is doing right now, what tools to use, and
  what done looks like.
- **Non-goals are first-class** — the spec captures "we will not build X" so agents can refuse scope drift instead of
  helpfully building it.
- **AC (acceptance criteria) are first-class citizens** — every behavior has at least one AC; AC is what "done" tests
  against.
- Workflow phases gate handoffs explicitly (no implicit "if it looks done, move on").
- Hard constraints are stated as constraints ("No functionality outside a journey"), not guidelines.

Anti-patterns:

- "You're a helpful assistant..." → no, you are the PO agent for this specific ticket, with these specific tools, in
  this specific phase, and you finish by calling `po_finish_brief`.
- Letting an agent decide its own scope.
- Implicit AC ("the user will know it when they see it").

---

## 4. Structured, consistent language

Agents understand their task better when **the language describing it is structured and consistent.** Free-form prose is
hard to parse reliably; named, anchored, validated structures are not.

There's a second, quieter reason for the structure: **a schema-shaped artifact reads to the agent as something it should
respect rather than rewrite.** A YAML file with a known shape, ids that are referenced from elsewhere, and a Pydantic
schema behind it gets treated as a contract. The same content as free-form markdown gets treated as a draft to be
improved. This is a hope more than a proven law, but the design leans on it.

There's a third reason that turns out to matter even more: **structured language doesn't just describe the work — it
shapes the work that produces it.** Every required field on a schema is a thinking prompt for the agent filling it in.
If the schema asks for a `simplest_solution` before a `proposed_solution`, the agent is forced to consider the dumb
baseline before earning any complexity — short-circuiting the most common agent failure mode of jumping straight to the
elegant-engineering answer pulled from training data. The flat `rationale` field would let the agent fill thinly; the
disciplined sequence (problem → simplest → complications considered) does cognitive scaffolding work even if nobody ever
reads the field afterward. This is why the intent- layer design (`docs/v2.0/agent-leverage/problem.md`) earns its keep — not
because the captured rationale is so valuable to consumers, but because the *act of filling the sequence* makes the
agent think better. Schema design as cognitive scaffolding, not just as data shape.

Implications:

- Every capability, behavior, persona, journey, non-goal has a **stable kebab-case id** (`{#post-a-job}`,
  `{#merchant}`).
- Every reference uses brackets (`[post-a-job]`) — distinct from definition syntax. Validators enforce both.
- A **URI scheme** addresses anything in the spec: `project://spec/suites/catalog/capabilities/normalize-skus`. Tickets,
  decisions, comments cite each other by URI.
- **Pydantic models** are the canonical shape — never trust raw YAML or markdown without validating through the schema.
- The same vocabulary appears across the codebase (`Capability`, `Behavior`, `Persona`, `Journey`, `Suite`, `Module`,
  `Contract`) and across role prompts. Synonyms are bugs.
- **Each project has its own ubiquitous language too** — the operator's domain vocabulary, captured during PO discovery
  in `.jig/spec/ontology.md` and read by every subsequent agent (PO continuing, SA, VD, PM, dev). When the operator says
  "blocker" we use "blocker" — not "obstacle," not "impediment," not "issue." Same discipline as jig's own vocabulary,
  just scoped to the project.

Anti-patterns:

- "I'll just call this thing 'feature'" — no, it's a `Capability`.
- Making up new words for old concepts.
- "The model will infer the structure from context" — no, give it the structure explicitly.
- Mixing rendering markdown with semantic markdown without anchors.
- Inventing terminology the operator didn't use ("early-warning intervention surface" instead of the operator's
  "looks-off signals"). The agent that does this once trains every other agent on the project to use the wrong word.

---

## 5. Both sides earn their best thinking through structure

Tenets 1-4 describe what makes **agents** effective: bite-sized work, exact context, clear instructions, structured
language. This tenet adds the complementary axis — what makes **humans** effective in this collaboration — and the
answer turns out to be the same structure, applied differently.

Agents and humans bring different strengths:

- **Agents** are good at execution, parallelism, perfect recall, and structured output — when given a well-framed
  problem.
- **Humans** are good at understanding the real world, judging tradeoffs, and reasoning about ambiguity — when given the
  right thinking scaffold.

The system's job is to put each side in the position where they do their best work. For agents, that means the bounded
context and disciplined prompts the prior tenets describe. For humans, it means **leading the operator through the right
thinking exercises** — sequencing questions so the load-bearing reasoning happens, structuring inputs so the operator
articulates what they're really deciding, and treating the operator as a partner in design rather than an approver of
agent output.

The same discipline that makes an agent fill `simplest_solution` honestly *also* makes a human articulate what problem
they're solving. The structure does cognitive work for both sides.

Implications:

- **Discovery loops are human thinking exercises**, not just data extraction. PO walks journeys before features so the
  operator considers users first. SA confirms contracts so the operator considers integration before implementation. PM
  reviews tier hints so the operator considers cost before dispatch.
- **The TUI is a thinking tool first, a command surface second.** Gates show what specifically is being evaluated;
  prompts sequence the right questions in the right order; operator overrides require structured reason categories, not
  just yes/no.
- **Required structured fields carry the load-bearing thinking.** Free-form prose is allowed for nuance, but the schema
  asks the questions that have to be answered. The same problem → simplest → complications sequence applies to
  human-authored artifacts.
- **Operator decisions are first-class and richly captured.** Every override emits an analytics event with a structured
  reason category; the system learns from operator judgment over time (eventually feeding the heuristics layer).
- **Humans live in the real world; agents don't.** When a decision requires real-world judgment (will users actually
  want this? does this fit our team's bandwidth? is the compliance team going to push back?), the system surfaces that
  decision *to the operator* with the right context, not to the agent.

Anti-patterns:

- Yes/no operator gates without surrounding structure ("approve? y/n").
- "Is this OK?" prompts that don't name what specifically the operator is being asked to evaluate.
- Treating the operator as a rubber stamp on agent output.
- Optimizing TUI for keyboard-shortcut speed at the expense of thinking flow.
- Asking the operator open-ended questions when a structured one would force clearer thinking.
- Making the agent decide things only a human can know (whether a real-world tradeoff matters; whether a stakeholder
  will accept; whether a design "feels right" given context the agent doesn't have).

---

## How the tenets compose

These tenets reinforce each other, but they aren't equal. Tenet 1 is the goal; 2, 3, and 4 are the levers we have on the
agent side; 5 is the lever we have on the human side.

- **Bite-sized work (1)** is only safe if context is **scoped (2)** — if a small task drags in the whole project to "be
  safe," it's not bite-sized anymore.
- **Coherence under decomposition (1)** depends on **structured language (4)** — without stable ids, federated specs,
  and shared vocabulary, the pieces can't agree on what they're each doing.
- **Clear instructions (3)** require **structured language (4)** so the constraints are themselves unambiguous, not
  narrative.
- **Exact context (2)** and **clear instructions (3)** together prevent the agent from filling silence with invention.
- **Structured language (4)** and **structured human input (5)** are the same discipline applied to different audiences.
  The schemas that shape agent thinking also shape human thinking; the TUI that exposes them is a thinking environment
  for both.
- **Human-side discipline (5)** prevents the failure mode where the operator becomes a rubber stamp — the system would
  still ship under tenets 1-4, but it would lose the real-world judgment that only humans bring.

When tenets pull in different directions, **tenet 1 wins.** If trimming context (2) means the agent will silently get a
behavior wrong, give it the context. If a "clearer" instruction (3) papers over a decomposition the agent can't actually
handle in one pass, break the work down further instead. If structured language (4) makes the operator-facing TUI worse
to think in, the operator- side cost (5) outweighs the agent-side benefit. The bar is whether the app ends up doing what
it's supposed to do — and that requires both sides bringing their best.

---

## How this design fits

The multi-level spec proposal (`docs/v2.0/multi-level-spec/`) was tested against these tenets as it was being designed:

- **Bite-sized + coherent (1):** L1 journey-driven discovery is slower than ad-hoc capability listing, but produces a
  complete capability roster with provenance — the operator can't accidentally skip a swath of the product, and the L3
  suite briefs each become a small, scoped task an agent can actually finish.
- **Exact context (2):** Suite-scoped briefs at L3 are the explicit embodiment of this tenet — a dev agent sees one
  suite, not the whole project.
- **Clear instructions (3):** Each level has its own PO mode with a focused prompt; "no functionality outside a journey"
  is a hard constraint stated explicitly.
- **Structured language (4):** Capability ids, journey ids, persona ids, the URI scheme extension, the federated
  structured spec — all the same anchoring discipline applied at the new layer, partly so the artifacts read as
  contracts rather than drafts.

When something fails the tenet check, kill the feature. When something clearly serves multiple tenets, prioritize it.
When something serves tenet 1 at the cost of one of the others, do it anyway and document the tradeoff.
