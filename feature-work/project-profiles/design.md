---
title: Project Profiles — Design
type: design
status: draft
owner: brent
created: 2026-05-20
updated: 2026-05-20
problem: ./problem.md
---

# Project Profiles — Design

## Summary

A project profile is a named YAML file that bundles two decisions: which SA role to run at init, and a
routing table mapping ticket size to a specific workflow YAML. Each workflow YAML is self-contained — it
defines all phases (spec, test, dev, review, validate) and embeds the reviewer list for its review phase.
Reviewer selection is therefore a property of the workflow, not a separate profile dimension. Different
profiles reference different workflow YAMLs for the same ticket size, so `medium`'s `s`-sized tickets
get a different workflow (and thus a different reviewer set) than `small`'s `s`-sized tickets.

Two built-in profiles ship with jig (`small`, `medium`). The selected profile is recorded in
`.jig/config.yaml` and governs all subsequent ticket dispatch. The profile is chosen once at start —
either via `--profile <name>` flag or via a `needs_info` stop where the PM describes the complexity
signals and asks the operator to choose. Auto mode does not guess; it only auto-proceeds if the profile
was passed explicitly.

## Approach

### Profile YAML format

Profiles live at `jig/defaults/profiles/<name>.yaml`. A profile is a routing table: `sa_role` selects
the SA depth; `workflows.default_by_size` maps ticket size to the workflow YAML that governs that size.
Each named workflow is self-contained — it defines phases and embeds its reviewer list.

```yaml
name: small
description: >
  Lightweight profile for simple projects: one or two modules, no external
  integrations, no compliance requirements. Uses the generic SA (no contracts)
  and the generalist reviewer. The only workflow distinction is xs (no test
  phase) vs everything else (test → dev → review[generalist] → validate).
  A good test bed for the orchestration pipeline.
sa_role: sa
workflows:
  default_by_size:
    xs: feature-xs          # dev → review[generalist] → validate
    s: feature-s            # test → dev → review[generalist] → validate
    m: feature-s            # same as s — size affects PM scoping, not workflow
    l: feature-s
  available:
    - feature-xs
    - feature-s
    - bugfix
    - validation
    - spike
```

```yaml
name: medium
description: >
  Standard profile for multi-module projects with external integrations,
  multiple datastores, or compliance requirements. Uses sa_mvp (produces
  per-module contracts.yaml) and the full specialist reviewer federation
  for non-trivial tickets.
sa_role: sa_mvp
workflows:
  default_by_size:
    xs: feature-xs          # dev → review[generalist] → validate (same as small)
    s: feature-s-full       # test → dev → review[full federation] → validate (NEW)
    m: default              # spec → test → review-tests → dev → review[full federation] → validate → document
    l: default
  available:
    - feature-xs
    - feature-s
    - feature-s-full
    - default
    - bugfix
    - migration
    - perf
    - validation
    - spike
```

### Template copy on selection

When a profile is selected, jig copies the profile definition and all its referenced workflow YAMLs
into the project:

```
jig/defaults/profiles/medium.yaml      → .jig/profiles/medium.yaml
jig/defaults/workflows/feature-xs.yaml → .jig/workflows/feature-xs.yaml
jig/defaults/workflows/feature-s.yaml  → .jig/workflows/feature-s.yaml
jig/defaults/workflows/feature-s-full.yaml → .jig/workflows/feature-s-full.yaml
jig/defaults/workflows/default.yaml    → .jig/workflows/default.yaml
...
```

The project runs entirely from its `.jig/` copies. The operator can edit any of these files to
customize behavior — changing reviewer lists, adding phases, adjusting acceptance criteria — without
touching the jig defaults. The defaults are templates, not runtime sources.

Load order for workflows and profiles at runtime:
1. `.jig/workflows/<name>.yaml` (project-local, operator-editable)
2. `jig/defaults/workflows/<name>.yaml` (fallback if not copied or missing)

This matches the existing role loading precedence (`.jig/roles/` before `jig/defaults/roles/`).

### New workflow: `feature-s-full`

`medium` profile's `s`-sized tickets need a workflow with the same phases as `feature-s` but with the
specialist federation at review time. This requires one new workflow YAML:

```yaml
name: feature-s-full
phases:
  - name: test
    role: test
    task_template: "Write tests for: {ticket_title}"
    acceptance_criteria: "Tests cover the specified behavior"
  - name: implement
    role: dev
    task_template: "Implement the feature so tests pass for: {ticket_title}"
    acceptance_criteria: "All tests pass"
  - name: review
    role: review
    task_template: "Review implementation for: {ticket_title}"
    acceptance_criteria: "No critical or important issues found"
    reviewers:
      - "reviewer-pattern-conformance"
      - "reviewer-error-handling"
      - "reviewer-performance"
      - "reviewer-security"
      - "reviewer-architectural"
  - name: validate
    role: validate
    task_template: "Validate the implementation for: {ticket_title}"
    acceptance_criteria: "All tests pass, linters clean"
```

The full federation runs at `s` size in the medium profile. Completeness and quality are the priority;
extra reviewer turns on a ticket that doesn't trigger findings is an acceptable cost.

### Config model changes

Add a `profile` field to `Config` and a `sa_role` field to `RolesSection`:

```python
class ProfileSection(BaseModel):
    name: str = ""          # "small", "medium", or custom name
    sa_role: str = "sa"     # role ID used by init_workflow
```

```python
class Config(BaseModel):
    profile: ProfileSection = Field(default_factory=ProfileSection)
    # ... existing fields unchanged
```

`ProfileSection.sa_role` defaults to `"sa"` so existing projects without a profile field continue to
work unchanged.

### Profile selection

**At `jig start`:**

1. If `--profile <name>` is passed, load the named profile, merge its `workflows` block into the config,
   set `config.profile.name` and `config.profile.sa_role`, and write `.jig/config.yaml`. No PM
   involvement.

2. If no `--profile` flag, the PM runs its normal planning phase. Before creating any tickets, the PM
   describes the project's complexity signals derived from the brief (scope, integrations, datastores,
   protocols, compliance) and raises `needs_info` with a profile recommendation and the list of available
   profiles. The operator responds; the orchestrator writes the selected profile to config.

3. In `--auto` / eval mode: if the profile was pre-specified (e.g. pinned in the brief's
   synthetic-operator answers), the `needs_info` is auto-answered. If not, the run halts — profile
   selection is not guessed.

### SA role selection at init

`init_workflow.py:run_sa_conversation` currently hardcodes `load_role(project_path, "sa")`. Change it to
read from config:

```python
cfg = load_config(project_path)
sa_role_id = cfg.profile.sa_role  # "sa" or "sa_mvp"
role_cfg = load_role(project_path, sa_role_id)
```

This is the only change needed to make SA depth profile-driven.

### Workflow resolution

No changes to `resolve_workflow()`. Profile application writes the profile's `workflows` block into
`.jig/config.yaml`, so the existing resolution logic picks it up automatically. Since each workflow
YAML embeds its own reviewer list, reviewer selection requires no additional profile machinery —
the right workflow for the ticket size carries the right reviewers with it.

### PM prompt additions

The PM role prompt gains two new sections:

> **Profile selection**: Before creating any tickets, assess the project's complexity using the signals
> in the brief. Look for: external service integrations, multiple datastores, uncommon protocols
> (gRPC, WebSockets), realtime requirements, auth/sessions/permissions, PII or payments, compliance
> requirements (HIPAA, SOC2, GDPR), high-availability SLOs, multiple writers to shared state,
> background job processing, public versioned APIs, event-driven architecture, or plugin systems.
> Use holistic judgment — no numerical thresholds. When in doubt, prefer the heavier profile.
> Call `ask_question` with your assessment and profile recommendation before creating any tickets.

> **Ticket sizing — contract impact**: A ticket that amends or enforces an architectural contract
> (ownership boundaries, integration AC, behavioral contracts, API surface) is never `xs` or `s`.
> Size it `m` or larger so it gets the full spec→test→review→validate workflow with architectural
> review. If a ticket starts as `s` and design reveals it touches a contract boundary, split it or
> upsize it before handing off to dev.

## Interfaces

### CLI

```
jig start [--profile <name>]
```

`--profile` is optional. When absent, PM raises `needs_info`. Valid values are built-in profile names
(`small`, `medium`) or names of files in `.jig/profiles/`.

### Profile YAML schema

```
name: str                           # required, must match filename stem
description: str                    # human-readable, shown in needs_info prompt
sa_role: str                        # required, must be a valid role ID
workflows:
  default_by_size: dict[str, str]   # size key → workflow name (workflow embeds reviewer list)
  available: list[str]              # optional allowlist of permitted workflow names
```

### `.jig/config.yaml` additions

```yaml
profile:
  name: small
  sa_role: sa
```

## Data model

`ProfileSection` is persisted inside the existing `Config` model. At profile selection time, the
profile and its workflows are copied from `jig/defaults/` into `.jig/`. After that point, the project
is self-contained — it does not read from `jig/defaults/` at runtime except as a fallback for files
that were not copied (e.g. `bugfix.yaml` if the operator deleted it).

New directories created in `.jig/` at profile selection:
- `.jig/profiles/` — the selected profile YAML
- `.jig/workflows/` — all workflow YAMLs referenced by the profile's `available` list

## Alternatives considered

### Embed profile settings directly in config.yaml without a profile name

Operators could just set `workflows.default_by_size` and `roles.sa_role` directly in `config.yaml`
without a named profile concept. This gives maximum flexibility but loses the PM's ability to recommend
a profile by name and makes the `needs_info` conversation harder to structure. The named profile
provides a shared vocabulary between the PM, operator, and system.

### Single profile field with numeric tiers (1, 2, 3)

Numbers are opaque — "use profile 2" tells the operator nothing about what they're choosing. Names
(`small`, `medium`) communicate intent. Operators can also define domain-specific names (`payments`,
`internal-tool`) if the built-ins don't fit.

### Chosen: named profile YAML files merged into config

Named files are inspectable, copyable, and customizable. The merge-into-config approach reuses the
existing `WorkflowsSection` and `resolve_workflow()` machinery without modification. The `ProfileSection`
addition to `Config` is minimal — two fields.

## Risks

- The PM's holistic judgment on profile selection may be wrong often enough to frustrate operators.
  Mitigation: the `needs_info` stop means the operator always sees the PM's reasoning and can correct it
  before any tickets are created. The cost of a wrong profile is recoverable (upscale triggers the
  onboarding sequence; downscale just costs a re-run).

- Existing projects have no `profile:` field — `ProfileSection` defaults to `name=""` and `sa_role="sa"`,
  which preserves current behavior. No migration needed.

## Out of scope

- Mid-project profile switching (depends on [[project-onboarding]] upscale sequence; separate feature).
- Per-ticket profile overrides.
- Adaptive process calibration (reviewer signal → automatic profile adjustment; future direction).
- Profile versioning or migration tooling.
- The `large` profile or any profile beyond `small` and `medium` for this iteration.

## Open questions

- [ ] Should the PM's `needs_info` profile question be a free-text response or a structured choice from
      the list of available profiles? Structured is easier to parse; free-text lets the operator ask
      follow-up questions about what each profile entails.
- [ ] Where does profile loading logic live — `cli.py` (at `jig start` time) or `init_workflow.py`
      (after the PM decides)? The `--profile` flag path belongs in `cli.py`; the PM `needs_info` path
      belongs in `init_workflow.py`. Both write to the same config field.

## Change log

- 2026-05-20: Initial draft (brent)
