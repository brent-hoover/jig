---
title: Project Profiles — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-20
updated: 2026-05-21
---

# Project Profiles — Problem Statement

## Context

Jig's orchestration stack has three independently configurable layers: the initialization workflow (which SA
role runs, what architectural artifacts get produced), the ticket workflow (which phases run per ticket —
test/dev/review/validate), and the reviewer federation (which specialist reviewers fire at review time).
These layers are currently wired separately and share no common configuration.

In practice, the depth of the SA role determines what artifacts downstream agents can use. `sa_mvp` produces
per-module `contracts.yaml` files that reviewer roles (`reviewer-error-handling`, `reviewer-architectural`,
`reviewer-performance`, `reviewer-security`) treat as authoritative input. The generic `sa` produces only
`architecture.yaml`. When a project is initialized with the generic `sa` (the current hard-coded default in
`init_workflow.py:1009`), those reviewer roles search for contracts that will never exist, wasting turns and
producing no AC-based findings.

There is also no concept of project "size" or "complexity" that flows through the system. Every project
gets the same reviewer federation regardless of whether it's a 200-line CLI or a multi-service platform.
The `reviewer-generalist` role (recently shipped) is a lighter-weight alternative for small tickets, but
there is no mechanism to select it automatically based on project characteristics.

## Problem

The three orchestration layers — SA depth, ticket workflow, reviewer selection — are misconfigured relative
to each other by default, and there is no mechanism to make them consistent. Specifically:

1. **SA/reviewer mismatch**: The generic `sa` does not produce `contracts.yaml`. The specialist reviewer
   roles assume contracts exist. On any project initialized with the generic `sa`, the error-handling,
   architectural, performance, and security reviewers cannot do AC-based analysis and waste turns searching
   for missing files.

2. **No project-size concept**: Every project gets the same workflow depth regardless of complexity. A
   200-line CLI gets the same multi-reviewer federation pass as a multi-module platform. There is no way
   for the PM or operator to declare "this is a small project; use lighter-weight agents."

3. **No upgrade path**: When a project turns out to be larger than initially expected, there is no
   mechanism to switch to a heavier configuration mid-project. The SA depth chosen at init time is fixed.
   Remaining tickets may warrant architectural review but have no contracts to review against.

4. **No user-visible knob**: Operators must understand the internal role/workflow system to make these
   choices. There is no single "project size" or "project type" concept they can set.

## Simplest possible solution

Add a `--profile <name>` flag to `jig start` that selects from a small set of named project profiles
(e.g. `small`, `medium`, `large`). Each profile is a YAML file that specifies which SA role to run, which
default ticket workflow to use, and which reviewer roles to activate. The PM/SA reads `profile:` from the
project config and uses it. No auto-detection, no mid-project switching.

This solves the wiring mismatch immediately. It requires the operator to make one explicit choice at start
time and doesn't help when they don't want to choose or when complexity changes mid-project.

## Complications considered

- **Scale**: N/A — the number of profiles is small and fixed; profile selection is a one-time operation.

- **Concurrency**: N/A — profile is read-only once set; no concurrent writers.

- **Failure modes**: If the PM chooses the wrong profile, downstream work is mis-scoped. Overestimating
  (chose `medium` for a project that turns out to be `small`) costs extra tokens but produces higher
  quality output — this is the acceptable failure direction. Underestimating (chose `small` for a project
  that turns out to be `medium`) is the dangerous direction: reviewers lack the inputs they need, AC-based
  checks don't fire, and quality degrades silently. When in doubt, the system should bias toward the
  heavier profile. Mid-project upgrade must not break in-progress tickets; upgrade should only affect
  tickets that haven't started their dev phase.

- **Cross-cutting policies**: N/A — profiles touch no PII, auth, or secrets.

- **Profile signal detection**: Profile selection uses two categories of signal derived from the brief
  (greenfield) or from the PO/SA onboarding read passes (existing projects):

  *Scope* — approximated by module and suite count. More modules and suites indicate a larger project
  that warrants deeper SA analysis and a fuller reviewer federation.

  *Complexity* — categorical yes/no signals that independently push toward a heavier profile regardless
  of scope:
  - External service integrations
  - Multiple datastores (e.g. PostgreSQL + Redis)
  - Uncommon protocols (gRPC, WebSockets)
  - Realtime requirements
  - Auth, sessions, or permissions
  - PII, payments, or health data
  - Compliance requirements (HIPAA, SOC2, GDPR)
  - High-availability or explicit SLO requirements
  - Distributed tracing / structured observability beyond basic logging
  - Multiple writers to shared state (concurrency, locking)
  - Eventual consistency or distributed transactions
  - Background job processing / async work queues
  - Public versioned API with external consumers
  - Event-driven / pub-sub architecture
  - Plugin or extension system

  The PM reads these signals holistically and uses judgment — no numerical thresholds. A single strong
  complexity signal (e.g. payments) may be sufficient to push a small-scope project to a heavier profile;
  a large-scope project with no complexity signals may stay light. The PM's assessment may be wrong;
  the operator corrects via `needs_info` response or explicit `--profile` flag.

- **Mid-project upgrade**: If a project was started as `small` and the operator upgrades to `medium`
  mid-run, modules built under the old profile have no contracts. The upgrade path is resolved by
  [[project-onboarding]]: when an upscale is triggered, the project halts and the onboarding sequence
  (PO → Spec → SA → test adequacy review) runs over existing artifacts to bring them up to the new
  profile's depth. This unifies mid-project upgrade with initial onboarding into a single mechanism.

- **Backwards compatibility**: Existing projects in the field have no `profile:` field. The system must
  behave sensibly when no profile is set — either by defaulting to a named profile or by falling back
  to current behavior.

## Constraints

- Profile selection must be expressible in a single YAML file that operators can copy and customize.
- The PM must be able to auto-select a profile without operator input and must be able to raise a
  `needs_info` if it cannot.
- Switching profiles mid-project must be a supported operation, even if the upgrade path is constrained
  (e.g. "upgrade applies to unstarted tickets only").
- No new external services or dependencies.

## Requirements

- A project profile bundles at minimum: SA role, default ticket workflow, reviewer set.
- A set of built-in profiles ships with jig (at minimum: small, medium). Operators can define custom
  profiles by copying and editing a built-in.
- The PM can select a profile automatically from brief content and raise `needs_info` when uncertain.
- The operator can specify a profile explicitly at `jig start` (overrides PM auto-selection).
- The selected profile is persisted in the project config so it governs all future tickets in the project.
- The operator can change the active profile mid-project; the change takes effect on tickets that have
  not yet entered the SA or dev phase.
- When no profile is specified and the PM cannot determine one, the system falls back to a default profile
  rather than failing.

## Non-goals

- Per-ticket profile overrides (profiles apply at the project level, not ticket level).
- Automatic mid-project profile upgrades without operator action.
- Dynamic profile selection based on runtime metrics (lines of code, test count, etc.) — brief content
  is the only signal available at selection time.
- Profile versioning or migration tooling.

## Future directions

- **Adaptive process calibration**: reviewer output as a mid-project feedback signal. If reviewers
  consistently produce no findings across multiple tickets, the system could propose downscaling the
  review depth for this project (saving tokens without sacrificing quality). Conversely, a pattern of
  critical findings could trigger an upscale recommendation. This is "self-healing project management"
  — the profile is not fixed at init time but evolves based on observed signal. Deferred; depends on
  profile switching infrastructure from this feature.

## Success criteria

- An hn-cli eval run initialized with the `small` profile uses the generic `sa`, `feature-xs`/`feature-s`
  ticket workflows, and `reviewer-generalist` — with no turns wasted searching for contracts.yaml.
- A hypothetical multi-module project initialized with the `medium` profile uses `sa_mvp`, `default`
  ticket workflows, and the full specialist reviewer federation — with contracts.yaml present and used.
- An operator who passes no `--profile` flag gets a working run without error; the PM selects a profile
  from the brief or falls back to a default.
- An operator who starts as `small` and upgrades to `medium` mid-project sees new tickets use the heavier
  workflow; completed tickets are unaffected.

## Open questions

- [x] PM profile selection: `needs_info` always fires unless the profile was explicitly specified at
      start time (e.g. `--profile small`). `--auto` mode does not allow the PM to guess — the operator
      must have pinned the answer in the brief or passed the flag for auto-proceed to apply.
- [ ] What axes define a profile? "Size" is ambiguous — a 200-line auth service may warrant deeper
      review than a 10k-line utility CLI. Relevant axes may include: module count, integration surface,
      security/compliance domain, team size, or expected change velocity. The right set of built-in
      profiles and their names depends on which axes matter and how they interact. Defer to design.
- [x] On mid-project upgrade: resolved — upgrade triggers the [[project-onboarding]] sequence over
      existing artifacts, bringing them to the new profile's depth before resuming ticket dispatch.
- [ ] How does the PM signal profile choice in the ticket/bus system so the orchestrator can write it to
      project config before dispatch begins?

## Change log

- 2026-05-20: Initial draft (brent)
- 2026-05-20: Resolved mid-project upgrade question via [[project-onboarding]] sequence (brent)
