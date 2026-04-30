# Jig Tenets

The four principles that govern every design decision in jig.

When you propose a feature, change, or refactor, check it against
these. If it can't be justified by them, it's probably not jig's job.

---

**Glossary** — terms used below: **AC** = Acceptance Criteria,
the testable one-sentence statement of what "done" means for a
behavior. **Spec** = the structured projection of the brief
(`.jig/spec/project.structured.yaml`). **Brief** = the markdown
authored by the PO agent (`.jig/spec/project.md`). Full ontology
in `ontology.md`.

---

## 1. Correctness over speed

Jig is a harness for coding agents optimized for **correct output, not
fast output.** A workflow that takes 20 minutes and produces working
code beats one that takes 4 minutes and produces half-broken code.

Implications:

- We will add up-front work (PO conversation, brief, spec, AC) when
  it makes the eventual code more correct.
- We will gate handoffs (brief approval, SA confirm, scaffold confirm)
  even when they slow the operator down — a prompt is cheaper than
  re-doing work.
- We will spend tokens on context that protects correctness.
- We will refuse to ship "looks fine, ship it" affordances. Verifiability
  is the floor.

Anti-patterns:

- "The model will probably figure it out" — no, give it what it needs
  to know it figured it out.
- Skipping AC because the behavior is "obvious."
- Optimizing model latency at the expense of structured outputs.

---

## 2. Exact context, no more

Agents perform best when given **exactly the context they need to do
their task — and no more.** Extra context dilutes attention, increases
hallucination risk, and burns tokens for no return.

Implications:

- Roles have **strict tool surfaces** (`strict_tools: true`) — they
  can only call what their job requires.
- Briefs are **scoped** — a dev agent on a catalog ticket reads
  catalog's module brief, not the whole product spec.
- Modules exist as a context-scoping primitive, not an architectural
  one. (See `docs/multi-level-spec/design.md`.)
- The structured spec is **federated** — looking up a single capability
  doesn't drag in the whole project.
- Decision records, comments, learnings get pulled in **on demand**
  via URIs, not flooded into the prompt.

Anti-patterns:

- "Just give the agent the whole project, it'll figure out what's
  relevant" — it won't, and even if it does, it'll cost 10× the tokens.
- Adding a context block "just in case."
- Making one big prompt that covers every situation; prefer phase- /
  role-specific prompts.

---

## 3. Clear instructions and boundaries

Agents perform best when given **clear instructions and explicit
boundaries.** Ambiguity in either direction (vague task, vague scope)
produces ambiguous output.

Implications:

- Every role has a phase-specific prompt that says exactly what the
  agent is doing right now, what tools to use, and what done looks like.
- **Non-goals are first-class** — the spec captures "we will not
  build X" so agents can refuse scope drift.
- **AC are first-class** — every behavior has at least one AC; AC is
  what "done" tests against.
- Workflow phases gate handoffs explicitly (no implicit "if it looks
  done, move on").
- Hard constraints are stated as constraints ("No functionality outside
  a journey"), not guidelines.

Anti-patterns:

- "You're a helpful assistant..." → no, you are the PO agent for
  this specific ticket, with these specific tools, in this specific
  phase, and you finish by calling `po_finish_brief`.
- Letting an agent decide its own scope.
- Implicit AC ("the user will know it when they see it").

---

## 4. Structured, consistent language

Agents understand their task better when **the language describing it
is structured and consistent.** Free-form prose is hard to parse
reliably; named, anchored, validated structures are not.

Implications:

- Every capability, behavior, persona, journey, non-goal has a
  **stable kebab-case id** (`{#post-a-job}`, `{#merchant}`).
- Every reference uses brackets (`[post-a-job]`) — distinct from
  definition syntax. Validators enforce both.
- A **URI scheme** addresses anything in the spec:
  `project://spec/modules/catalog/capabilities/normalize-skus`.
  Tickets, decisions, comments cite each other by URI.
- **Pydantic models** are the canonical shape — never trust raw
  YAML or markdown without validating through the schema.
- The same vocabulary appears across the codebase (`Capability`,
  `Behavior`, `Persona`, `Journey`, `Module`) and across role prompts.
  Synonyms are bugs.

Anti-patterns:

- "I'll just call this thing 'feature'" — no, it's a `Capability`.
- Making up new words for old concepts.
- "The model will infer the structure from context" — no, give it
  the structure explicitly.
- Mixing rendering markdown with semantic markdown without anchors.

---

## How the tenets compose

These tenets reinforce each other:

- **Correctness (1)** depends on **clear instructions (3)** and
  **structured language (4)** so the agent knows what to produce.
- **Exact context (2)** enables **correctness (1)** by reducing the
  surface area where the agent can go wrong.
- **Structured language (4)** enables **exact context (2)** by making
  it possible to slice the spec by URI.
- **Clear instructions (3)** require **structured language (4)** so
  the constraints are themselves unambiguous.

When tenets appear to conflict, **correctness (1) wins.** If exact
context (2) and correctness (1) disagree — e.g., the agent might need
slightly more context to catch an edge case — give it the context.
But the burden of proof is on the additional context.

---

## How this design fits

The multi-level spec proposal (`docs/multi-level-spec/`) was tested
against these tenets as it was being designed:

- **Correctness (1):** L1 journey-driven discovery is slower than ad-
  hoc capability listing, but produces a complete capability roster
  with provenance — the operator can't accidentally skip a swath of
  the product.
- **Exact context (2):** Module-scoped briefs at L3 are the explicit
  embodiment of this tenet — a dev agent sees one module, not the
  whole project.
- **Clear instructions (3):** Each level has its own PO mode with a
  focused prompt; "no functionality outside a journey" is a hard
  constraint stated explicitly.
- **Structured language (4):** Capability ids, journey ids, persona
  ids, the URI scheme extension, the federated structured spec — all
  the same anchoring discipline applied at the new layer.

When something fails the tenet check, kill the feature. When something
clearly serves multiple tenets, prioritize it.
