---
title: Project Onboarding — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-20
updated: 2026-05-20
---

# Project Onboarding — Problem Statement

## Context

Jig currently assumes greenfield projects: an operator provides a brief, the PM breaks it into tickets,
the SA runs architectural setup, and development proceeds against a blank codebase. All orchestration
artifacts (`.jig/`, `architecture.yaml`, `contracts.yaml`) are created from scratch at initialization
time.

Most real-world codebases are not greenfield. An operator may want to bring jig to an existing service,
a legacy application, or a codebase mid-development. Today, that operator has no path in — jig has no
way to ingest an existing project, and attempting to run it against one would produce a greenfield brief
that ignores everything already built.

This feature is being designed alongside [[project-profiles]], which defines how SA depth, ticket
workflows, and reviewer selection are bundled into a named profile. The synergy is significant: for a
greenfield project, profile selection is informed by the brief alone (the code doesn't exist yet). For
an onboarding project, the codebase itself is the ground truth — the SA can observe actual complexity,
module count, existing test coverage, and integration surface to select a profile with far higher
confidence than brief analysis allows.

## Problem

There is no supported path to use jig on an existing codebase. Specifically:

1. **No ingestion mechanism**: `jig start` requires a brief and assumes nothing exists. There is no
   command or flow for pointing jig at a directory that already contains code.

2. **No read-mode for PO or SA**: The PO produces suites during a brief interview with the operator;
   the SA makes architectural decisions before code is written. Neither has a mode for reading an
   existing codebase and extracting what is already there. Onboarding requires both roles to run in
   "read mode" — PO extracts suites from existing behavior, SA extracts modules and contracts from
   existing structure.

3. **No backlog bootstrapping from existing state**: For greenfield, the PM creates tickets from the
   brief. For an existing project, the useful backlog is the delta between current state and desired
   state — bugs, missing features, tech debt, test gaps. There is no mechanism to derive that delta.

4. **Profile selection is harder without a brief**: Greenfield profile selection (see [[project-profiles]])
   uses brief content as signal. For onboarding, the brief may be thin or absent entirely, but the
   codebase provides richer signal. Profile selection and onboarding are naturally coupled — the SA
   that reads the existing code is also best positioned to recommend a profile.

## Onboarding sequence

Onboarding retrofits the artifacts that the normal greenfield flow produces, but starting from code
rather than requirements. The agents are the same roles used in greenfield — PO, Spec, SA, and the
test adequacy reviewer — but invoked with prompts customized for reading existing code rather than
making forward decisions. The sequence:

1. **(Optional) Brief interview** — a conversation with the operator to capture the big-picture view:
   what the system is, what it does, where it is heading. Produces or refines `brief.md`. Skippable
   if the operator already has a brief or wants to defer.

2. **PO read pass** — the PO reads the existing codebase and produces suites: capability groups and
   user-facing behaviors. This is the same artifact the PO produces during a greenfield brief interview,
   but derived from what is already built.

3. **Spec pass** — the Spec agent turns the PO's suites into specs: EARS-style acceptance criteria
   grounded in existing behavior rather than desired behavior.

4. **SA read pass** — the SA reads the existing codebase and produces modules: architectural boundaries,
   ownership, integration surfaces, and (if the profile calls for it) per-module `contracts.yaml`
   derived from existing behavior.

5. **Test adequacy review** — the test adequacy reviewer assesses current test coverage against the
   specs produced in step 3, producing a gap report rather than blocking findings.

6. **Profile selection** — based on what PO and SA observed (suite count, module count, integration
   surface, test gap severity), the appropriate project profile is selected. The operator can override.

7. **Operator review** — the operator reviews all produced artifacts before any backlog is created.

8. **PM backlog bootstrap** — if a brief exists, the PM derives an initial ticket backlog from the
   delta between current state (suites + modules + test gaps) and desired state (brief). If no brief,
   deferred.

## Upscaling as onboarding

The same sequence is used when a project upscales to a larger profile mid-run. When an upscale is
triggered, the project halts, and the onboarding sequence runs over the existing artifacts — updating
suites, specs, modules, and contracts to match the new profile's depth. This unifies two otherwise
separate problems (initial onboarding, mid-project profile change) into a single mechanism.

## Simplest possible solution

A `jig onboard <path>` command that runs steps 2–6 sequentially (PO → Spec → SA → test adequacy →
profile selection), pauses for operator review, then optionally runs the PM backlog bootstrap. Brief
interview deferred to v2.

## Complications considered

- **Scale**: The SA read-mode pass must handle codebases of arbitrary size. A 50k-line monorepo takes
  many more turns than a 200-line CLI. Profile selection should cap the depth of this pass — a `small`
  profile triggers a shallow read; a `medium` or `large` profile triggers a deeper module-by-module
  walk. Without this cap, onboarding cost is unbounded.

- **Concurrency**: N/A — onboarding is a one-time sequential operation; no concurrent writers during
  the SA read pass.

- **Failure modes**: The SA may misread an existing codebase — it might miss modules, misidentify
  boundaries, or produce an `architecture.yaml` that doesn't match reality. This is tolerable if the
  operator can correct it before tickets are created. The onboarding flow should include an operator
  review step before the PM generates the backlog. If the SA crashes mid-onboard, the `.jig/` directory
  may be partially initialized; the onboard command must be safely re-runnable.

- **Cross-cutting policies**: The existing codebase may contain secrets, credentials, or PII in source
  files. The SA read pass must not log or persist file contents beyond what is needed for architectural
  analysis. This is the same constraint as any agent with `Read` access, but worth naming explicitly
  given the SA will be reading arbitrary existing code.

- **Existing tests and contracts**: An existing codebase may have its own integration tests, API
  contracts (OpenAPI specs, protobuf definitions), or architecture docs. The SA should use these as
  inputs to `architecture.yaml` rather than ignoring them. Mapping existing artifacts to jig's schema
  is non-trivial; the design must decide how much of this mapping is automated vs operator-provided.

- **Backlog bootstrapping**: Deriving a meaningful backlog from "existing state vs desired state" is
  inherently judgment-heavy. The PM can do this, but it needs both the SA's read of current state and
  a brief describing desired state. If the operator has no brief, the PM has no delta to work from.
  The design should allow onboarding without a brief (producing only the `.jig/` structure and
  architecture, deferring backlog creation) as well as with one.

- **Synergy with project-profiles**: Profile selection during onboarding can use actual codebase
  signals (file count, module count, test coverage, presence of external integrations) rather than
  brief language alone. This makes auto-selection more reliable than the greenfield case and reduces
  the need for operator input. The SA read pass and profile selection should be designed as a single
  operation, not two sequential steps.

## Constraints

- The `jig onboard` command must be safely re-runnable without corrupting partial state.
- The SA read pass must be depth-bounded by the selected profile to prevent unbounded cost.
- Onboarding must not require a brief — it should produce a usable `.jig/` structure even when the
  operator has no desired-state document yet.
- No external services beyond what jig already uses.

## Requirements

- `jig onboard <path>` initializes jig against an existing codebase at `<path>`.
- The SA runs in read mode, analyzing existing code to produce `architecture.yaml` and, if the profile
  calls for it, per-module `contracts.yaml` derived from existing behavior.
- Profile selection during onboarding uses codebase signals in addition to (or instead of) brief
  content; the operator can override.
- The operator can optionally provide a brief describing desired future state; if provided, the PM
  generates an initial backlog from the current-state/desired-state delta.
- If no brief is provided, onboarding completes with `.jig/` structure and architectural artifacts
  only; the operator can add a brief later to generate a backlog.
- The onboard flow includes an operator review of the SA's architectural output before the PM runs.
- The command is safely re-runnable; partial state from a previous failed run is detected and handled.

## Non-goals

- Automatic migration of existing issue trackers or project management tools into jig tickets.
- Automatic discovery of existing test failures or bugs (the SA reads structure, not runtime behavior).
- Retroactive ticket creation for work that has already been completed.
- Support for non-git repositories.

## Success criteria

- An operator can run `jig onboard` against the hn-cli project directory and get a populated `.jig/`
  with `architecture.yaml` and the correct profile selected, ready for new tickets to be created.
- An operator can run `jig onboard` against a larger multi-module project and get a `medium` profile
  auto-selected with per-module `contracts.yaml` produced.
- An operator who provides a brief during onboarding gets an initial backlog of tickets representing
  the gap between current state and desired state.
- Re-running `jig onboard` on a partially initialized project does not corrupt existing state.

## Open questions

- [ ] Are the PO and SA onboarding passes new roles (`po-onboard`, `sa-onboard`) or variants of the
      existing roles invoked with a different prompt/context? New roles are simpler to prompt correctly
      but duplicate maintenance surface; variants are harder to prompt without contaminating the
      greenfield prompts.
- [ ] The brief interview (step 1) is optional in v1 — but what triggers the operator to provide a
      brief later if they skipped it? Does jig prompt them the next time they try to create a ticket?
- [ ] For profile auto-selection: the PO and SA read passes produce signals (suite count, module count,
      integration surface). Which agent makes the profile recommendation — the SA at the end of its
      pass, or a separate step after both passes complete?
- [ ] How does onboarding interact with an existing `.jig/` directory? Re-onboard (update artifacts
      in place, preserving existing tickets) vs reject with a clear error?
- [ ] What is the operator review UX — a `needs_info` pause where the operator edits files directly,
      or a structured review conversation with the PO/SA?

## Change log

- 2026-05-20: Initial draft (brent)
- 2026-05-20: Added PO→SA read sequence and brief interview step; resolved SA-only framing (brent)
