---
id: REQ-INIT-SCAFFOLD
title: Template application and architecture.yaml finalization
type: spec
status: draft
owner: brent
created: 2026-04-24
updated: 2026-04-24
depends_on: [REQ-INIT-SA]
implements: [../problem.md]
---

# Template application and architecture.yaml finalization

## Context

Scaffold is the terminal action of `jig init`. It applies the
chosen template to the project directory, finalizes
`architecture.yaml` (populating required keys regardless of
whether SA or the direct path produced it), records the template
decision in `.jig/project.yaml`, and emits a `scaffold_applied`
SystemEvent on the architecture ticket.

Scaffold runs after SA confirmation (SA path) or after the user
picks from a numbered list (direct path). The required
architecture keys must be present and correct on both paths so
that downstream context hydration has a uniform contract.

See `../design.md` §Step-by-step steps 9–10, §Interfaces → File
formats → architecture.yaml, §Risks → half-applied scaffold,
§Out of scope → transactional scaffolding.

## Requirements

### REQ-INIT-SCAFFOLD.1 (event-driven)

When the user confirms a scaffold (SA path `Y`, or direct-path
template selection), the CLI shall apply the template contents
to the project directory.

**Acceptance:** Files declared by the template appear in the
project directory after scaffold. The `.jig/` subtree is
preserved and not overwritten by template content.

### REQ-INIT-SCAFFOLD.2 (ubiquitous)

After template application succeeds, the CLI shall update
`.jig/project.yaml` with `template_name` and
`template_applied_at` (ISO8601 UTC).

**Acceptance:** Reloading `.jig/project.yaml` shows both fields
present with correct values. A subsequent `jig init` on the
same directory errors per REQ-INIT-CLI.4.

### REQ-INIT-SCAFFOLD.3 (ubiquitous)

After template application succeeds, the CLI shall ensure
`.jig/spec/architecture.yaml` contains `template`,
`template_applied_at`, `sa_path`, `language`, and `framework`
keys at minimum.

**Acceptance:** `architecture.yaml` parses, every required key
is present and non-empty, and the file's final content is
identical across SA and direct paths for those five keys given
the same template.

### REQ-INIT-SCAFFOLD.4 (event-driven)

When the SA path produced an `architecture.yaml` that lacks
`language`, `framework`, or `deploy_target`, the CLI shall
backfill those fields from template metadata at scaffold time.

**Acceptance:** An SA run that set only `rationale` and
`data_stores` results in a post-scaffold architecture.yaml
whose `language` and `framework` match the template's metadata.
Fields SA did set are not overwritten.

### REQ-INIT-SCAFFOLD.5 (event-driven)

When the direct path is used, the CLI shall write
`architecture.yaml` from template metadata only, populating
`template`, `template_applied_at`, `sa_path: false`,
`language`, `framework`, and (if the template declares one)
`deploy_target`. No `rationale`, `config`, or scoping fields
shall be written.

**Acceptance:** A direct-path architecture.yaml contains
exactly the six (or five, if the template has no
`deploy_target`) keys above and no others.

### REQ-INIT-SCAFFOLD.6 (event-driven)

When scaffold completes, the system shall emit a
`scaffold_applied` SystemEvent on the architecture ticket.

**Acceptance:** `jig story architecture` shows a
`scaffold_applied` event as the terminal entry after any SA
thread content.

### REQ-INIT-SCAFFOLD.7 (ubiquitous)

Templates shall carry metadata declaring at minimum
`language`, `framework`, and optional `deploy_target`, exposed
to the CLI for architecture.yaml population.

**Acceptance:** The v1 template registry returns a dict with
those keys for any installed template. A template missing
`language` fails to install.

### REQ-INIT-SCAFFOLD.8 (unwanted behavior)

If template application fails partway, the CLI shall not
update `.jig/project.yaml`, shall not write
`scaffold_applied`, and shall exit non-zero with a message
pointing the user at `--force`.

**Acceptance:** A simulated mid-scaffold failure leaves
`project.yaml` without `template_applied_at` and no
SystemEvent; re-running `jig init` detects the inconsistent
state and errors per REQ-INIT-CLI.15.

### REQ-INIT-SCAFFOLD.9 (ubiquitous)

`sa_path` in `architecture.yaml` shall be `true` on SA-path
runs and `false` on direct-path runs.

**Acceptance:** Running both paths against the same brief/spec
produces architecture files that differ in `sa_path` value and
in the presence of SA-only fields.

### REQ-INIT-SCAFFOLD.10 (unwanted behavior)

If the user attempts a direct-path scaffold with a template
name not in the registry, the CLI shall reject the selection
and re-display the list.

**Acceptance:** An invalid template choice does not create an
architecture.yaml or emit any SystemEvent; the user is
re-prompted.

## Explicit non-requirements

- Transactional scaffold with rollback. Half-applied scaffold
  errors with `--force` as the escape hatch. Staging /
  rollback is deferred.
- Multi-template scaffolds. One template per init run.
- Template specification — directory layout, config-parameter
  schema, scaffold hooks beyond metadata. See `../design.md`
  §Out of scope → Template specification.
- Streaming UI for template application progress.
- Post-scaffold verification (e.g. running the template's
  test suite to confirm it works). Out of scope for this
  sub-project.

## Open questions

- [ ] Whether `config` from `sa_propose_scaffold` should be
  merged into template metadata at apply time, or passed to
  the template as a separate parameter set. Plan to decide
  during implementation.
- [ ] Exact v1 template set (likely python-api, python-cli,
  typescript-cli).

## Change log

- 2026-04-24: Initial draft (brent)
