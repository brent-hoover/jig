---
title: Binary review severity & failed-ticket dead-ends — Deferred Work
type: notes
status: active
owner: brent-hoover
created: 2026-06-12
updated: 2026-06-12
---

# Binary review severity & failed-ticket dead-ends — Deferred Work

Living catalog of work deferred during implementation — nice-to-haves, edge cases, and anything descoped to ship
sooner. Append as we go. At `/close-feature` every item is resolved: done now, kept deferred, or permanently dropped.

## Deferred items

### Phase-loop integration test for SA escalation wiring

**What:** A test that drives `_run_ticket`'s blocked branch end-to-end: persistent key past threshold triggers
exactly one adjudication spawn; cap-trip escalates instead of failing directly; `MAX_SA_ESCALATIONS` exhausts to
direct fail; SA guidance lands in the next round's fix bundle.

**Why deferred:** No existing test harness drives the phase loop (worktrees + agent spawns + workflow YAML are all
live in `_run_ticket`); building one is its own piece of work. Every component the branch composes is unit-tested
(`_run_sa_adjudication` verdict paths + fail-closed, `_keys_past_survival_threshold`, binding-dismissal gate filter,
persistence counting, guidance prompt rendering), and step 7's hn-cli eval run exercises the wiring live.

**Impact:** Nice-to-have. A regression in the branch's composition (not its components) would currently surface in
eval runs rather than unit tests.

**Status:** Deferred

**Revisit when:** A phase-loop test harness exists, or the first time the escalation wiring regresses in an eval.
