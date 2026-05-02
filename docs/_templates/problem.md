---
title: <Feature Name> — Problem Statement
type: problem
status: draft
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# <Feature Name> — Problem Statement

## Context

<What's the current situation? What's in place today, and what's prompting this work? One or two paragraphs. Orient someone who's never seen this problem before.>

## Problem

<What specifically is wrong, missing, or needed? Be concrete. Avoid proposing solutions here — that's the design doc's job. If the problem is "X is broken," describe the breakage, not the fix.>

## Simplest possible solution

<Before considering complications, what's the most obvious, dumbest thing that would solve the problem as stated? Not the elegant answer; the *simplest* answer. This section exists to catch over-engineering at the source. If the proposed design ends up significantly more complex than this, the complications below have to earn it.>

<Often the answer is something like "store it in a JSON file" or "do nothing different — the existing thing handles it." If that's true, say so honestly.>

## Complications considered

<For each complication, state: does it actually apply to this problem, and if so, what does it force? Most complications won't apply. Those that don't should be explicitly marked "N/A: <why>" rather than skipped — silence on a complication is indistinguishable from "we didn't think about it.">

- **Scale**: <does the problem grow non-linearly with users, data volume, project size, agent count? If so, what changes
  about the solution? If not: "N/A — bounded by <thing>".>
- **Concurrency**: <multiple agents, processes, or operators acting on the same state? Race conditions, ordering
  guarantees, locking? If not: "N/A — single-writer / serialized / read-only".>
- **Failure modes**: <what happens when the simplest solution breaks — disk full, network partition, agent crash
  mid-operation, malformed input? Which failures are tolerable, which need explicit handling? If trivially handled: "N/A
  — fail-loud with no recovery needed".>
- **Cross-cutting policies**: <does this touch PII, auth, secrets, audit, observability, or other system-wide rules?
  Which apply, and what do they force the solution to do? If none apply: "N/A — touches none".>

<Add complications specific to this problem if they don't fit the four above (e.g. cost, latency, backwards-compatibility, multi-language support). Each gets the same treatment: does it apply? what does it force?>

## Constraints

<What are we operating under that constrains any solution? Performance requirements, deployment environment, existing systems we must integrate with, regulatory/compliance requirements, team capacity, timeline.>

-
-
-

## Requirements

<The "must be true" statements. What properties must any solution have? Prefer observable, checkable statements. Don't design here — describe the shape of an acceptable answer.>

-
-
-

## Non-goals

<What is explicitly OUT of scope? This section is load-bearing. The things you don't do are as important as the things you do.>

-
-
-

## Success criteria

<How will we know the work is done and the problem is solved? What does "good" look like? If you can't answer this, the problem isn't well-understood yet.>

-
-
-

## Open questions

<Things that need answers before design can start. Anything blocking should be resolved here, not deferred into implementation.>

- [ ]
- [ ]

## Change log

- YYYY-MM-DD: Initial draft (<owner>)
