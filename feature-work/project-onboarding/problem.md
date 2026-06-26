---
title: Project Onboarding — Problem Statement
type: problem
status: superseded
owner: brent-hoover
created: 2026-05-20
updated: 2026-06-26
superseded_by: ../../architecture/plan.md
---

# Project Onboarding — Problem Statement

> **Superseded.** Absorbed into `architecture/plan.md` Epic 6 (Discovery
> engine — onboarding extracted as `jig/engines/discovery/onboard.py`).
> Brownfield TB support is a design target, not a requirement.

# Project Onboarding — Problem Statement

## Context

Jig currently assumes greenfield projects: the operator provides a brief, the PM breaks it into tickets, the SA
runs architectural setup, and development proceeds against an empty codebase. All orchestration artifacts
(`.jig/`, `architecture.yaml`, `contracts.yaml`, `boundaries.yaml`) are created from scratch at initialization
time via `jig init`.

Most real-world codebases are not greenfield. Operators may want to bring jig to an existing service, a legacy
application, or a codebase mid-development. Today there is no path in — attempting to run jig against an
existing project would produce a greenfield brief that ignores everything already built.

This feature is being designed alongside the [[sa-architect]] work (unified SA role, `TechDecision` model,
Context7/WebFetch/WebSearch research tools, `BoundariesFile` per-module boundaries) which is in progress. The
onboarding SA read pass will be able to leverage those research capabilities, but sa-architect must land first
— that is an explicit prerequisite. Project profiles have shipped: `jig/defaults/profiles/small.yaml` and
`medium.yaml` define the two active profiles that drive SA depth and reviewer selection. Onboarding is the
natural place where codebase signals (file count, module count, test coverage) can drive profile auto-selection
with higher confidence than brief-only analysis allows.

## Problem

There is no supported path to use jig on an existing codebase. Specifically:

1. **No ingestion command**: `jig start` requires a brief and assumes nothing exists. There is no command or
   flow for pointing jig at a directory that already contains code and producing a usable `.jig/` structure.

2. **No read-mode for PO or SA**: The PO produces suites from a brief interview; the SA makes architectural
   decisions before code is written. Neither role has a prompt mode for reading an existing codebase and
   extracting what is already there. Onboarding requires both roles to run in "read mode" — the PO extracts
   suites and user behaviors from existing behavior, the SA extracts modules, contracts, and boundaries from
   existing structure.

3. **No backlog bootstrapping from existing state**: For greenfield, the PM creates tickets from the brief. For
   an existing project, the useful backlog is the delta between current state and desired state — bugs, missing
   features, tech debt, test gaps. There is no mechanism to derive that delta.

4. **Profile selection is harder without a brief**: Greenfield profile selection uses brief content as signal.
   For onboarding, the brief may be thin or absent entirely, but the codebase provides richer signal. The SA
   that reads the existing code is also best positioned to recommend a profile, but there is no mechanism to
   wire codebase observations into the selection decision.

## Simplest possible solution

Add an `--onboard` flag to `jig init`. Seed a synthetic brief-approved state and inject a system note into
the SA ticket telling it to read existing code rather than design new architecture. No new command, no scanner
agent, no `observations.md`.

This falls short: the generic `sa` role uses `sa_propose_scaffold` (template-pick flow) — there is no
scaffold to pick against existing code. The PO interview is skipped entirely, producing no suite extraction.
There is no operator review gate before the PM runs. The design ends up being more complex than this because
those gaps have to be closed for onboarding to be useful.

## Complications considered

- **Scale**: The SA read pass must handle codebases of arbitrary size. A 50k-line monorepo requires far more
  turns than a 200-line CLI. Onboarding cost grows with codebase size and is unbounded without a cap mechanism
  — the design must specify one.
- **Concurrency**: N/A — onboarding is a one-time sequential operation; no concurrent writers during the read
  passes.
- **Failure modes**: The SA may misread an existing codebase — it may miss modules, misidentify boundaries, or
  produce an `architecture.yaml` that does not match reality. This is tolerable only if the operator can
  correct it before tickets are created. If the SA crashes mid-onboard, the `.jig/` directory may be
  partially initialized; the command must be safely re-runnable. If the operator skips review and tickets are
  created from a bad architectural read, the errors propagate silently into every subsequent dev ticket.
- **Cross-cutting policies**: The existing codebase may contain secrets, credentials, or PII in source files.
  The SA read pass must not log or persist file contents beyond what is needed for architectural analysis.

## Constraints

- The `jig onboard` command must be safely re-runnable without corrupting partial state from a previous
  failed run.
- The SA read pass must be depth-bounded to prevent unbounded token cost.
- Onboarding must not require a brief — it should produce a usable `.jig/` structure even when the operator
  has no desired-state document yet.
- No external services beyond what jig already uses.
- Non-git repositories are not supported.

## Requirements

- `jig onboard` initializes jig against an existing codebase.
- The PO runs in read mode, deriving suites from existing behavior rather than from a brief interview, and
  produces a syntactically valid suite artifact that the operator reviews and confirms before continuing.
- The SA runs in read mode and produces a syntactically valid `architecture.yaml`, per-module `contracts.yaml`,
  and `boundaries.yaml` derived from observed import structure; the operator reviews and confirms before any
  backlog is created.
- Profile selection during onboarding uses codebase signals (suite count, module count, integration surface,
  test gap severity) in addition to or instead of brief content; the operator can override.
- Onboarding includes an operator review of produced artifacts before any backlog is created.
- If the operator provides a desired-state brief, the PM generates an initial backlog from the current-state/
  desired-state delta. If no desired-state brief is provided, onboarding completes with `.jig/` structure and
  architectural artifacts only.
- Re-running `jig onboard` on a partially initialized project does not corrupt existing state.

## Non-goals

- Automatic migration of existing issue trackers or project management tools into jig tickets.
- Automatic discovery of existing test failures or runtime bugs (the SA reads structure, not runtime
  behavior).
- Retroactive ticket creation for work already completed.
- Support for non-git repositories.

## Success criteria

- An operator can run `jig onboard` against an existing Python project and get a populated `.jig/` with
  `architecture.yaml` and the correct profile selected, ready for new tickets.
- An operator can run `jig onboard` against a multi-module project and get a `medium` profile auto-selected
  with per-module `contracts.yaml` produced.
- An operator who provides a desired-state brief during onboarding gets an initial backlog of tickets
  representing the gap between current state and desired state.
- Re-running `jig onboard` on a partially initialized project does not corrupt existing artifacts or tickets.
- The operator review step occurs before the PM generates any tickets.

## Open questions

- [ ] Are the PO and SA onboarding passes new roles (`po-onboard`, `sa-onboard`) or variants of the existing
      roles invoked with a different prompt/context? New roles are simpler to prompt correctly but duplicate
      maintenance surface; variants are harder to prompt without contaminating greenfield prompts.
- [ ] How does onboarding interact with an existing `.jig/` directory? Re-onboard (update artifacts in place,
      preserving existing tickets) vs. reject with a clear error?
- [ ] What is the operator review UX — a `needs_info` pause where the operator edits files directly, or a
      structured review conversation with the PO/SA?
- [ ] For profile auto-selection: which agent makes the profile recommendation — the SA at the end of its
      read pass, or a separate step after both PO and SA passes complete?
- [ ] What defines the "current-state/desired-state delta" that the PM converts into a backlog? Multiple
      requirements and success criteria depend on this reconciliation existing (PO/SA read artifacts as
      current-state, desired-state brief as desired-state), but the mechanism by which the PM identifies
      ticket-able gaps is unspecified.

## Change log

- 2026-05-20: Initial draft (brent)
- 2026-05-20: Added PO→SA read sequence and brief interview step; resolved SA-only framing (brent)
- 2026-06-07: Restructured to canonical template; removed design content (sequence, upscaling, simplest
  solution); updated context to reference sa-architect and boundaries work; applied reviewer feedback —
  clarified project-profiles shipped, sa-architect is in-progress prerequisite; made SA requirements
  falsifiable via operator review gate; dropped solution-leaking Scale clause; added delta-reconciliation
  open question; boundaries derivable from code exploration — removed non-goal, made boundaries.yaml a
  first-class SA read-mode output (Brent Hoover)
