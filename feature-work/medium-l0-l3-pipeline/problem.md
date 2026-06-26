---
title: Medium L0–L3 PO Pipeline — Problem Statement
type: problem
status: superseded
owner: brent-hoover
created: 2026-06-09
updated: 2026-06-26
superseded_by: ../../architecture/plan.md
---

# Medium L0–L3 PO Pipeline — Problem Statement

> **Superseded.** Absorbed into `architecture/plan.md` Epic 6 (Discovery
> engine — L0-L3 pipeline becomes the Discovery interview at architectural
> resolution).

# Medium L0–L3 PO Pipeline — Problem Statement

## Context

A `jig init` runs a Product Owner (PO) elaboration, then a Systems Architect (SA), then scaffolding. jig
has two PO topologies:

- **v1 (flat)** — a single PO conversation produces one monolithic `docs/brief.md` and a flat
  `.jig/spec/project.structured.yaml`. The v1 SA (`sa.yaml`) reads that flat spec and applies a single
  scaffold. No suite/module decomposition happens.
- **v2 (L0–L3)** — four progressively-detailed PO levels: **L0** project pitch (`docs/brief.md`), **L1**
  discovery / personas / journeys / capability roster (`.jig/spec/discovery.md`), **L2** suite
  organization (`.jig/spec/suites.yaml`), **L3** per-suite structured specs
  (`.jig/spec/suites/<id>/spec.structured.yaml`). The module-producing SA (`sa_mvp.yaml`) reads these and
  emits per-module `contracts.yaml` (and, since the just-shipped module-boundaries work, `boundaries.yaml`).

The v2 machinery is **built and tested**: all four PO roles (`l0_po.yaml`–`l3_po.yaml`), their MCP handler
modules (`po_l0_mcp.py`–`po_l3_mcp.py`) with finalize + handoff chaining (L0→L1→L2→L3→SA), ontology
tracking, handler-level tests, and an end-to-end scenario (`tests/scenarios/bones-with-l1-discovery`). The
`sa_mvp` role is complete. `classify_resume` already recognizes the SA's `arch_finalize` handoff (#137).

Both shipped profiles (`small.yaml`, `medium.yaml`) currently set `sa_role: sa` (the v1 flat path).

## Problem

For a real `jig init`, the v2 L0–L3 pipeline is **unreachable** — the init orchestration never drives it.
`classify_resume` (`init_workflow.py`), the state machine that routes `jig init`, only knows the v1 `brief`
path; it has no states for discovery / suites / per-suite specs and no spawn helpers for L1/L2/L3 (only the
v1 `run_po_conversation`). The only way to produce the L0–L3 artifacts today is the manual `/init --proceed`
TUI command, which steps one level at a time by hand, or hand-written fixtures in test scenarios.

The consequence: **medium projects never get module decomposition.** Even though `sa_mvp`, per-module
contracts, the specialist reviewer federation, and module-import-boundary enforcement all exist and are
tested, none of them activate for a normally-initialized medium project — `medium.yaml` still resolves to
the v1 flat SA, which produces a single undifferentiated scaffold. The entire module-oriented half of jig
(including the module-boundaries feature just merged) sits inert for real use because the spec that feeds
it is never produced.

This work makes the L0–L3 pipeline the **default** init path for the `medium` profile, so a medium
`jig init` produces the suite/module decomposition `sa_mvp` needs end-to-end.

## Complexity drivers

- **Scale**: N/A — runs once per project init; bounded by capability/suite count (single digits in
  practice). No per-request or per-user scaling dimension.
- **Concurrency**: N/A — init is a single, sequential, operator-driven flow; single-writer over the spec
  artifacts.
- **Failure modes**: The init flow goes from **one** PO step to **four-plus** (L0, L1, L2, then L3 once per
  suite), so there are many more points where init can be interrupted, stall, or be resumed. Resume
  correctness matters: a run stopped mid-pipeline must continue at the right level, not restart or skip
  (L1 already carries its own resume state machine for exactly this reason). A missing or malformed
  artifact at any boundary breaks the next level or the SA downstream. And this **changes the default init
  behavior for every new medium project** — a regression here strands medium init entirely.
- **Cross-cutting policies**: N/A — no PII / auth / secrets. One obligation is observability: the operator
  must be able to see where they are in the L0→L3 progression, since it's now a multi-stage flow rather
  than a single brief.

## Constraints

- Must **reuse** the existing L0–L3 roles + MCP handlers and the `sa_mvp` role — they are built and tested;
  this is wiring, not a rebuild.
- Must integrate with the existing init state machine (`classify_resume` / `ResumeState`) and the existing
  init agent-spawn pattern (`run_po_conversation`), not a parallel orchestrator.
- The flow must be **resumable** — `jig init` can stop and resume mid-pipeline (interrupted runs are
  expected, not exceptional).
- The **`small` profile (and any non-medium profile) keeps the v1 flat PO + SA *mechanics*** (the
  `brief → approval → spec_generation → SA(sa)` flow) — the L0–L3 change is scoped to `medium`. The one
  accepted exception: because the profile is now selected up front (so the topology can branch before the
  PO), the **interactive PM-1 profile-selection pass is retired** for all profiles, small included — the
  operator's up-front size choice is authoritative (exactly as `--profile` already behaves).
- `sa_mvp` and the v1 `sa.yaml` are both retained; this activates `sa_mvp` for medium, it does not delete
  the v1 path.

## Requirements

- A normal `jig init` on a `medium` project drives the operator through L0→L1→L2→L3 PO elaboration and then
  the module-producing SA, producing `discovery.md`, `suites.yaml`, per-suite `spec.structured.yaml`, and
  per-module `contracts.yaml` — with **no** manual `/init --proceed` stepping required.
- The medium init flow is resumable: an interrupted run continues at the correct level.
- `small` (and other non-medium) init is unchanged — verified by a regression check.
- The `medium` profile resolves `sa_role` to the module-producing SA.

## Non-goals

- Building or substantially changing the L0–L3 PO roles / MCP handlers / `sa_mvp` — already done.
- Changing the `small` profile's PO + SA *mechanics* or the v1 flat PO path (beyond retiring the
  interactive PM-1 profile-selection pass, which moves up front for all profiles).
- Deleting `sa_mvp` or the v1 `sa.yaml`.
- Module-boundaries enforcement — already shipped; it activates as a consequence of modules being produced,
  no new work here.
- Deciding the exact operator UX for how levels are driven (auto-cascade vs. per-level confirmation gates)
  — that is a design decision, captured as an open question, not fixed here.

## Success criteria

- `jig init <medium-project>` end-to-end produces `docs/brief.md`, `.jig/spec/discovery.md`,
  `.jig/spec/suites.yaml`, and `.jig/spec/suites/<id>/spec.structured.yaml`, then `sa_mvp` produces
  `.jig/spec/modules/<m>/contracts.yaml` — without any manual `/init --proceed` steps.
- An init interrupted mid-pipeline resumes at the correct level (e.g. stop after L1, resume → continues at
  L2, not L0).
- A `small` (and any non-medium) `jig init` still resolves `sa_role: sa` and drives the v1
  `brief`→approval→spec→SA flow with no L1/L2/L3 states — confirmed by the existing small init
  test/scenario passing (updated only for up-front profile selection — the PM-1 profile pass no longer
  runs). Behavioral invariant on the PO+SA mechanics, not a literal byte diff (init outputs carry per-run
  timestamps/UUIDs).
- An end-to-end test/scenario covers the full medium auto-init chain (L0→L1→L2→L3→`sa_mvp`).

## Open questions

- [ ] **Resume state machine (the first design fork)**: what states does `ResumeState` gain for L1/L2/L3,
  and how does `classify_resume` detect a level's completion — via handoff/SystemEvent markers in the
  ticket thread (as the v1 `brief` path does) or artifact-on-disk presence (as `/init --proceed` does)?
  Resume correctness across an interrupted multi-level run hinges on this. (Design.)
- [ ] **Operator UX**: does medium `jig init` auto-cascade through L0→L3 (spawning each level back-to-back),
  or gate at each level with an operator confirmation (mirroring the existing brief-approval /
  scaffold-confirm prompts)? The existing `/init --proceed` (`_proceed`) already implements the L0→L3
  gating logic the auto path needs — it's a reusable reference (factor shared gating helpers rather than
  duplicate), not just something to deprecate. (Design.)
- [ ] **Missing-artifact behavior**: how should `sa_mvp` behave if a level's artifact is absent or
  malformed — hard-fail the init, or degrade with a visible warning? (Design.)
- [ ] **L3 fan-out**: L3 runs once per suite. Does init drive all suites' L3 in sequence automatically, and
  how is per-suite progress tracked/resumed? (Design.)

## Change log

- 2026-06-09: Initial draft (brent-hoover)
