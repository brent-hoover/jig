---
title: The Living Invariant — Entity Model
type: reference
status: draft
owner: brent-hoover
created: 2026-06-21
updated: 2026-06-26
---

# The Living Invariant — Entity Model

## One sentence

> Architecture is the **traceable mapping from intent to structure**, written in a **shared vocabulary**,
> that **constrains what agents may produce** — and stays honest by being reconciled and measured.

## The two ladders

The project's root (its pitch) sits at the head of **two** descents that must **converge at Capability**:

```
            PITCH + PILLARS
            /            \
      value ladder    user ladder
           |               |
        Pillar          Persona
           |               |
           |            Journey
            \             /
             →  CAPABILITY  ←     (must be reachable from BOTH)
                    |
                Contract  →  Code
```

- **Value ladder** (down from the pillars): *what must this do to deliver the promise?*
- **User ladder** (down from personas): *who needs what, in what flow?*

**Convergence test (an invariant):** a Capability is legitimate only if reachable from *both* ladders. From a
pillar but no persona → gold-plating. From a persona but no pillar → scope creep.

## Entities

### Intent (outside-in) — what must be true for users

- **Persona** — who the system serves
- **Journey** — an end-to-end path a persona takes to an outcome
- **Capability** — a discrete thing the system can do (serves ≥1 journey *and* ≥1 pillar). Carries a
  **required User Story** (`As [persona], I want Y, so that [value]`) — the narrative form of its trace edges —
  plus **Behaviors + AC**. The required user story makes orphan capabilities impossible by construction.
- **Behavior** — the testable assertion of a capability (Given/When/Then)
- **Suite** — a coherent grouping of capabilities/behaviors (a slice of the system)

### Structure (inside-out) — how it's built

- **Boundary** — a unit with an inside and an outside; has exactly one owner
- **Contract** — what a boundary exposes across its edge: API / Event / Data / Behavioral
- **Dependency** — an *allowed* edge: boundary A may consume contract C of boundary B

### Spine — the part missing today

- **Ontology** — the shared nouns; every artifact references ontology terms (one definition, not two)
- **Trace** — the edges linking the two halves: Capability `realized_by` Contract/Boundary; Behavior
  `verified_by` Suite

> **Two ontologies, don't conflate them.** There is Jig's **own** meta-vocabulary (the names for Jig's
> moving parts — canonical home is the top-level `ontology.md`) and the **per-project** `Ontology` entity
> above (the nouns of the system Jig is *building*). The duplicate `OntologyTerm` in `schemas/arch.py` vs
> `schemas/po.py` is a fork of the *per-project* one.

## The invariants (the actual payload)

The model exists to make these checkable:

1. **Coverage** — every Capability is realized by ≥1 Contract; every Journey is covered by ≥1 Suite. No
   orphans either direction.
2. **Conformance** — the code honors its Contracts; declared Behaviors pass.
3. **Containment** — no Boundary reaches into another except through a declared Dependency on a declared
   Contract.
4. **Vocabulary / one concept, one home** — every term used anywhere exists in the Ontology, and every
   concept has exactly one canonical home. This makes the duplicate-`OntologyTerm`, the six overlapping
   feature dirs, and the three scattered principle docs the *same violation at different layers* — not
   accidents. It is measurable (count concepts with >1 home) → a real fitness function. This invariant is the
   mechanical defense of the north-star, **simplicity & clarity**.
5. **Ownership** — every Boundary has exactly one owning role.

## Scope boundaries (what the model deliberately does NOT capture)

- **Structural, not behavioral-internal.** The model says a Boundary exists, what it exposes, and what it may
  depend on. It says nothing about *how* the boundary works inside — that's the agent's job, fenced by the
  contract. Keeps the model from becoming a second copy of the code.
- **Process is not architecture.** The Code Factory Loop (Spec→Test→Dev→Review→Validate→Document) is
  *workflow*. It references the invariant but is a separate model. Side by side, not merged.

## Lifecycle & enforcement layering

- **Authored** — the interview (`interview.md`).
- **Enforced** — mechanical first, semantic only where unavoidable:
  - *Structure / boundaries / dependencies* — deterministic (import graphs, deny-lists; runtime via bwrap).
  - *Contracts* — schema/conformance checks.
  - *Trace / coverage* — **deterministic graph queries once trace edges are first-class data.** Orphan
    capability, orphan contract, uncovered journey are all graph checks, not LLM judgments.
  - *Semantic residue* — reviewer LLM only for "does this code actually fulfill this behavior" and proposing
    new trace edges. Never trusted as truth on its own.
- **Reconciled** — declared model diffed against real code; drift surfaced as work.
- **Measured** — evals confirm enforcement works and the architecture is any good (the Metrics store).

> Lever: every trace edge stored as *data* moves an intent-check from the LLM column to the deterministic
> column. "Mechanical as much as possible" is raised by investing in trace-as-graph.
