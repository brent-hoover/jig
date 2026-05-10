---
title: <Feature Name> — Design
type: design
status: draft
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
problem: ./problem.md
---

# <Feature Name> — Design

## Summary

<One paragraph. What is the proposed approach? Someone reading only
this section should know roughly what we're going to build.>

## Approach

<The chosen design, in enough detail to implement against. Cover the
main components, their responsibilities, and how they interact. Diagrams
welcome. Do not describe every function — that's what the code is for.>

## Interfaces

<External-facing APIs, CLI surfaces, file formats, wire protocols.
The things other code or other humans will depend on. Changes to these
are expensive later, so pin them down now.>

## Data model

<If there's persistent state or structured data, describe its shape.
Skip this section if there isn't.>

## Alternatives considered

<What else did we look at, and why did we not choose it? This is the
most valuable section of any design doc — it prevents the "why didn't
you just..." conversation six months from now. One paragraph per
alternative, including the one we chose.>

### <Alternative 1>

<What it was, why we rejected it.>

### <Alternative 2>

<What it was, why we rejected it.>

### Chosen: <the chosen approach>

<Why this one won.>

## Risks

<What could go wrong? What assumptions are we making that, if invalidated,
break this design? What's the blast radius if this is wrong?>

-
-
-

## Out of scope

<What this design does NOT address, even though it might seem related.
Prevents scope creep during implementation.>

-
-
-

## Open questions

<Design-level questions still unresolved. Blocking questions should be
answered before implementation starts.>

- [ ]
- [ ]

## Change log

- YYYY-MM-DD: Initial draft (<owner>)
