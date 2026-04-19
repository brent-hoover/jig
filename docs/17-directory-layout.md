# 17 — Directory Layout and Template Catalog

The canonical layout for both the jig source tree and a project using
jig. Consolidates paths that have been referenced piecemeal across
earlier docs, and specifies the template catalog model.

## Premise

Prior docs introduce paths as they become relevant —
`spec/` in [02](./02-project-spec.md), `context/project/` in
[07](./07-context-bundles.md), decision records in
[04](./04-ownership.md), policy concerns in
[16](./16-policy-and-enforcement.md). No single record collects them.
Implementers need one.

This doc fixes two concrete decisions and consolidates the rest:

1. The user-visible root is `.jig/`. Matches the CLI name and the
   existing code.
2. Shipped defaults live under `jig/defaults/` in the core package
   and layer with project-specific overrides at well-known paths.

## Two trees

**Core repo** is the jig source tree. Contains the service, CLI,
sandbox machinery, and shipped-default templates. Users never see or
edit this directly — it's the installed package.

**Project repo** is the codebase that uses jig. Contains the project
spec, project-specific overrides, context bundles, decisions, and the
closed-ticket archive under `.jig/`. This is what users see and
edit.

Both trees are addressed by this doc because the layering between
them is part of the contract.

## Core repo: `jig/defaults/`

```
jig/
  defaults/
    roles/                  # shipped role templates
      dev.yaml
      reviewer.yaml
      spec.yaml
      planner.yaml
      qa-validator.yaml
      security-reviewer.yaml
      po-helper.yaml
      sa-helper.yaml
      ...
    workflows/              # shipped workflow definitions
      hotfix.yaml
      small-change.yaml
      standard.yaml
      large-feature.yaml
      epic.yaml
      ...
    project_templates/      # starter scaffolds for `jig init`
      python/
      fastapi/
      ...
  skills/                   # internal skill library (unchanged)
  ... (python modules)
```

Three kinds of shipped artifact:

- **Role templates** — per [06](./06-agent-identity.md). One YAML per
  template. Includes regular phase actors (dev, reviewer, spec), agent
  checks (qa-validator, security-reviewer), and human-with-helper
  helpers (po-helper, sa-helper). Flat — the directory doesn't
  classify by caller, because how a template is invoked (phase vs
  check vs helper) is a caller concern, not a template-shape concern.
- **Workflow definitions** — per [05](./05-workflow-model.md). One YAML
  per workflow. The shipped set covers the t-shirt sizes and a few
  opt-ins.
- **Project scaffolding templates** — used by `jig init` to lay down
  a starter codebase. Unrelated to role templates beyond sharing the
  word; kept alongside for discoverability and packaging simplicity.

Renames from the current code: `agent_types/` → `roles/` (matches doc
terminology), and the root-level `templates/` moves to
`jig/defaults/project_templates/` (groups shipped artifacts in one
place). Code changes to realize these moves are a separate task.

## Project repo: `.jig/`

```
.jig/
  config.yaml               # project-level configuration
  spec/
    project.md              # PO-authored product spec (Markdown)
    project.structured.yaml # spec-agent maintained structured form
  roles/                    # project role-template overrides
    dev.yaml
    frontend-dev.yaml       # project-specific variant
    ...
  workflows/                # project workflow overrides
    standard.yaml
    ...
  checks.yaml               # content-policy check catalog
  context/
    project/                # curated project-wide context artifacts
      architecture.md
      conventions.md
      domain-glossary.md
    roles/
      reviewer/
        checklist.md
      dev/
        guidance.md
  decisions/                # decision records
    DR-0001-service-shape.md
    DR-0002-sandbox-model.md
    ...
  archive/                  # closed tickets, JSONL
    TKT-0001/
      thread.jsonl
      checkpoints.jsonl
      handoffs.jsonl
      checks.jsonl
      manifest.yaml
    TKT-0002/
      ...
```

Every subdir and file corresponds to an artifact type introduced in
an earlier doc. Cross-reference table:

| Path | Introduced by |
|---|---|
| `spec/` | [02](./02-project-spec.md) |
| `roles/` | [06](./06-agent-identity.md) |
| `workflows/` | [05](./05-workflow-model.md) |
| `checks.yaml` | [10](./10-verification.md) |
| `context/project/` | [07](./07-context-bundles.md) |
| `context/roles/<role>/` | [07](./07-context-bundles.md) |
| `decisions/` | [04](./04-ownership.md), [08](./08-threads.md) |
| `archive/` | [12](./12-service-shape.md) |
| `config.yaml` | scattered |

No `.jig/policy/` directory. Policy lives with its entity per
[16](./16-policy-and-enforcement.md) — capability policy on role
templates, process policy in workflow definitions, content policy in
`checks.yaml`. No separate artifact needs a dedicated directory.

## Resolution order

When the service needs a role template or workflow by name:

1. Look under the project repo (e.g., `.jig/roles/dev.yaml`). If
   present, use it.
2. Fall back to the shipped default
   (`jig/defaults/roles/dev.yaml`). If present, use it.
3. Fail at load — not at runtime. `jig start` validates the full
   workflow and role catalog on startup; missing references surface
   immediately.

The project-repo file replaces the shipped default entirely — no
merge, no patch. Teams that want to tweak a shipped template copy it
into `.jig/roles/` as a starting point and edit. A scaffolding command
(`jig role init dev`) copies the shipped default into the project
repo for convenience.

Full override keeps versioning straightforward — one file, one source
of truth — and makes behavior obvious from reading the project repo.
The cost is re-declaring unchanged fields when tweaking; the benefit
is that the project's effective configuration is always visible
in-place. Merge/extends semantics are [deferred](#deliberately-deferred).

## Template naming

Role template files are named by the logical role (or variant):
`dev.yaml`, `frontend-dev.yaml`, `security-reviewer.yaml`. The
filename (without extension) is the name a workflow phase or check
catalog references:

```yaml
# workflow phase
- name: implement
  role: dev
  assignment:
    spawn_agent: frontend-dev   # references .jig/roles/frontend-dev.yaml
```

```yaml
# check catalog
security-review:
  type: implementation_aware_agent
  template: security-reviewer   # references .jig/roles/security-reviewer.yaml
```

Names are lowercase, hyphen-separated. No suffixes like `-template` or
`-role` — the directory already classifies.

## Check catalog

Single file at `.jig/checks.yaml` carrying all three check types:
scripted, implementation-aware agent, black-box agent. Per-check
files are deferred until projects hit painful scale (most projects
have fewer than 20 checks; one file is easier to navigate).

Shape per [10](./10-verification.md) — commands, types, templates,
context declarations, severity, timeouts.

Shipped defaults for checks are intentionally minimal — beyond
truly-generic checks (basic lint, type-check shape) most content
policy is project-specific. Projects build their own catalog; the
harness provides a template but doesn't pre-populate meaningfully.

## Archive format

Each closed ticket archives to `.jig/archive/<ticket-id>/` as a
directory of JSONL files plus a manifest:

- `manifest.yaml` — ticket metadata (id, size, work type,
  workflow, open timestamp, close timestamp, final status, linked
  capability, parent/child references).
- `thread.jsonl` — every thread entry, append-order.
- `checkpoints.jsonl` — every checkpoint from every phase attempt.
- `handoffs.jsonl` — handoff entries with evaluator decisions.
- `checks.jsonl` — check runs with verdicts and output.

JSONL matches the existing serialization conventions in the codebase
and is easy to diff, grep, and process with external tools. The
manifest provides the single-read summary; the JSONL files are for
deep dives.

The service writes the archive on ticket closure per
[12 — Service shape](./12-service-shape.md) §State location,
committing under its own git identity. Archives are append-only from the service's
perspective — humans can read them but the service doesn't re-open
closed tickets.

## Project config

`.jig/config.yaml` is the top-level project configuration. Carries:

```yaml
project:
  name: my-project
  scm:
    adapter: github          # GitHub, GitLab, ...
    repo: org/my-project
    # credentials live in the service, not here

workflows:
  default_by_size:
    xs: hotfix
    s: small-change
    m: standard
    l: large-feature
    xl: epic
  available: [hotfix, small-change, standard, large-feature, epic]

ownership:
  spec:
    behaviors: po
    acceptance_criteria: po
    design: sa
    technical_risks: sa
  architecture: sa
  capability_policy: sa
  process_policy: sa
  content_policy: sa
  roadmap: po
  # ...

roles:
  po:
    assignment: human_with_helper
    human: "alice@example.com"
    helper_template: po-helper
  sa:
    assignment: human_with_helper
    human: "bob@example.com"
    helper_template: sa-helper

escalation:
  default_human: "alice@example.com"
```

SCM credentials, auth tokens, and other secrets do not live in
`config.yaml` — they're service-side only per
[12](./12-service-shape.md) and [13](./13-scm-integration.md).

## Repo vs service

What's a file under `.jig/` vs what's in service state (SQLite per
[12](./12-service-shape.md) §State location):

| Repo (`.jig/`) | Service (SQLite) |
|---|---|
| Project spec, both formats | Live ticket state |
| Role templates, workflows | Active agent instances |
| Check catalog | Thread entries (until closure) |
| Context bundle artifacts | Checkpoints (until closure) |
| Decision records | Scheduling / queue state |
| Closed-unit archives | Auth tokens, heartbeats |
| `config.yaml` | In-flight SCM state |

On ticket closure, the service serializes the ticket's live
state into a new `.jig/archive/<ticket-id>/` directory and commits it.
After that, the archive is the durable record; the service can
purge live state.

## Validation at load

`jig start` (and `jig validate` as a dry-run) walks the layout once
and fails loud on:

- Unknown role names referenced from workflows.
- Unknown check names referenced from workflows.
- Unknown workflow names in `config.yaml`'s `default_by_size` or
  `available`.
- Missing required context URIs declared in role templates (e.g., a
  role references `project://architecture` and the artifact doesn't
  exist).
- Malformed YAML in any catalog file.
- Circular template references (when extends/merge lands — until
  then, not applicable).

Runtime resolution should never fail for these reasons. Discoveries
happen at load; the service refuses to start with an invalid catalog.

## Test project and fixtures

The core repo keeps a `test-project/` directory at the root with a
minimal `.jig/` for integration tests. Not documented here beyond
naming — it's a fixture, not part of the shipped surface.

## What this does for the original problems

- **Problem 3** (agents arrive undereducated): canonical paths for
  context bundles mean agents always know where to find project-
  level knowledge. No hunting.
- **Problem 4** (no consistent memory): decision records have a
  declared home; closed tickets archive to a declared home.
  Memory lives in predictable places.
- **Problem 5** (hard to scale across work sizes): catalog-based
  workflows and role templates scale by adding files, not by
  rewriting code. A team adds a new work type by dropping a
  workflow in `.jig/workflows/` and referencing it in
  `config.yaml`.

## Deliberately deferred

- **Template extends/merge.** Full override is explicit and simple.
  Merge semantics are a natural v2 if re-declaration becomes
  painful.
- **Per-check-file catalog.** Single `checks.yaml` for v0.2;
  splitting into per-check files is a refactor, not a redesign.
- **Variant composition.** Templates are full copies; no mixin or
  composition mechanism. Teams that want shared capability lists
  across templates can factor them into role-level context
  artifacts or wait for extends/merge.
- **Alternate archive formats.** JSONL + manifest is the shipped
  format. Teams wanting alternative formats (Parquet for analytics,
  SQLite export, etc.) would build them as post-processors rather
  than re-shaping the archive.
- **Multi-workspace layouts.** Monorepos with multiple projects
  would want separate `.jig/` roots per project. Out of scope —
  one project per repo per [12](./12-service-shape.md).
- **Live service state export.** `.jig/` is repo-durable; live
  service state stays in SQLite. A dump command for debugging
  exists implicitly (open the SQLite file) but isn't a first-class
  artifact.
