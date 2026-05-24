---
title: Deterministic Ticket Spec — Implementation Plan
type: plan
status: archived
owner: brent
created: 2026-05-24
updated: 2026-05-24
design: ./design.md
---

# Deterministic Ticket Spec — Implementation Plan

## Overview

Six steps in dependency order: build the pure materialization function first (testable in isolation), wire it
into `handle_create_ticket` (non-breaking, ticket creation still works without `derived_from`), remove the now-
redundant `spec` phase from the `default` workflow, update the `test` role to consume the materialized spec,
update the PM role prompt to pass `derived_from`, and finally update `reviewer-test-adequacy` to use the
structured spec instead of parsing prose so AC coverage gaps become deterministic findings.

## Preconditions

- [x] Design approved
- [x] Clean test baseline on the worktree (4105 passed)
- [x] Worktree on `feat/deterministic-ticket-spec`

## Steps

### 1. Add `materialize_ticket_spec_from_capability` helper

**What:** New pure function in `jig/specs.py`:

```python
def materialize_ticket_spec_from_capability(
    *,
    ticket_id: str,
    work_type: WorkType,
    size: Size,
    capability: Capability,
) -> TicketSpec:
    """Build a TicketSpec by projecting a Capability's AC fields into the
    work-type's expected shape. Pure function — does not write to disk."""
```

Maps capability fields onto `TicketSpec.fields`:

- `summary` ← `capability.summary`
- `behaviors` ← `[b.model_dump(mode="json") for b in capability.behaviors]`
- `acceptance_criteria` ← `capability.acceptance_criteria`
- `out_of_scope` ← `capability.excluded`

Skips fields the capability doesn't carry. The work-type schema validation happens later in
`save_ticket_spec`; this function is pure shape-mapping.

**Why:** Isolates the mapping logic for unit testing and keeps `ticket_mcp.py` thin.

**Verify:** New unit tests in `tests/test_specs.py`:
- Maps a populated capability into a `TicketSpec` with all four fields present.
- Behaviors are serialized as plain dicts (round-trips through yaml).
- Empty `capability.excluded` produces `out_of_scope: []` (not missing).
- Empty `capability.acceptance_criteria` with non-empty behaviors still produces a valid `TicketSpec`
  (because behaviors carry per-behavior AC).
- `uv run pytest tests/test_specs.py -v` passes.

### 2. Wire materialization into `handle_create_ticket`

**What:** In `jig/ticket_mcp.py`:

- Add `derived_from = args.get("derived_from")` to the ticket kwargs.
- After `tickets.create(...)` returns the `ticket_id`, if `derived_from` matches
  `project://spec/capabilities/{id}` and `project_path` is set:
  - Try to `load_structured_spec(project_path)`. On `FileNotFoundError`, log debug and continue.
  - `cap = spec.capability_by_id_or_alias(cap_id)`. If `None`, log warning and continue.
  - Build a `TicketSpec` via step 1's helper.
  - Try `save_ticket_spec(project_path, ticket_spec)`. On `SpecValidationError`, log warning with details
    and continue — best-effort enrichment.
- Order: this block runs *after* the ticket exists in the store but *before* the bus publish, so any
  downstream subscriber that reads the spec sees it on the first event.

**Why:** Makes spec materialization happen automatically at ticket creation for every workflow size.

**Verify:**
- New integration test in `tests/test_ticket_mcp.py`:
  - Setup: write a minimal `project.structured.yaml` with one capability into a tmp project.
  - Call `handle_create_ticket(args={"work_type":"feature","title":"...","derived_from":"project://spec/capabilities/foo",...})`.
  - Assert `.jig/specs/<ticket_id>.yaml` exists and its `fields` match the capability.
- Regression: existing `handle_create_ticket` tests still pass with no `derived_from` (no spec written).
- Negative test: bogus `derived_from` URI logs a warning but ticket is still created.
- `uv run pytest tests/test_ticket_mcp.py tests/test_specs.py -v` passes.

### 3. Remove `spec` phase from `default` workflow

**What:** Edit `jig/defaults/workflows/default.yaml`:

- Delete the leading `spec` phase block.
- Workflow now starts with `test`, ending with `document` (unchanged).

**Why:** Spec is materialized at ticket creation; running an agent to re-create it would be wasted turns.

**Verify:**
- `uv run pytest tests/ -k workflow` passes.
- Manually inspect the file — no remaining `spec` phase reference in `default.yaml`.

### 4. Add `ticket://spec` to `test` role context

**What:** Edit `jig/defaults/roles/test.yaml`:

- Add `ticket://spec` to `default_context`.
- Update `phase_prompt` to instruct: "Read the ticket spec at the start. Every item in
  `behaviors[*].acceptance_criteria` and `acceptance_criteria` is a required test target. Test names should
  reference the AC item they cover."

If `default_context` already contains `ticket://spec`, only the prompt is updated.

**Why:** The test agent needs to know to use the materialized spec as its contract; otherwise it still works
from prose.

**Verify:**
- `uv run pytest tests/ -k role` passes.
- `uv run pytest tests/ -k prompt_builder` passes.
- Manually inspect the rendered test prompt with a sample ticket spec to confirm the section appears.

### 5. Update PM role prompts to pass `derived_from`

**What:** Find the PM role prompts (`pm.yaml`, `planner-pm.yaml`) and the planner-PM MCP write_build_plan
docs. Add explicit instruction: when creating a ticket from a capability in the project spec, pass
`derived_from="project://spec/capabilities/<capability_id>"` to `create_ticket`.

**Why:** Without this, the PM has no reason to populate `derived_from` and the new materialization path stays
dormant for evals.

**Verify:**
- Inspect the affected role YAML files.
- Run the hn-cli eval; confirm new feature tickets have non-null `derived_from` in the ticket store and
  matching `.jig/specs/<id>.yaml` files.
- Confirm the test agent prompt now embeds the AC items from the materialized spec.

### 6. Update `reviewer-test-adequacy` to consume the structured spec

**What:** Edit `jig/defaults/roles/reviewer_test_adequacy.yaml`:

- Replace `ticket://description` with `ticket://spec` in `default_context`. The reviewer only runs in the
  `default` workflow, which only runs on medium+ feature tickets, which always have `derived_from` — so a
  spec is always present. Missing spec is a system bug, not a graceful-degradation case.
- Rewrite "What counts as an AC" and "Mechanics":
  - Source of truth is `ticket://spec` — items in `behaviors[*].acceptance_criteria` and top-level
    `acceptance_criteria`.
  - If `ticket://spec` is empty or missing: file a single `critical` finding ("ticket spec missing — system
    bug") and stop. No prose-parsing fallback.
  - Each spec AC item without a covering test → `important` finding (was `notable` under prose parsing).
  - Drop the long "An AC item is not" disambiguation — the structured spec is unambiguous.
- Keep Section 2 (consistency / test-code hygiene) unchanged.

**Why:** The eval analysis found RC-7-style coverage gaps escaping as `notable` advisories that dev never
addressed. With a deterministic AC list, untested items become non-negotiable findings instead of subjective
judgements.

**Verify:**
- `uv run pytest tests/ -k reviewer` passes.
- Re-run the hn-cli eval after all prior steps are in. Confirm:
  - Findings against AC items are emitted at `important` severity.
  - The reviewer cites AC items by their text from `ticket://spec` rather than inferring them from prose.

## Rollback

Each step is independently revertable. If the materialization causes regressions, revert step 2 (the
`handle_create_ticket` wiring) — the helper from step 1 sits dormant, no behavior change. To restore the spec
phase, revert step 3. No data migration is required at any point — `.jig/specs/` files are regenerable from
the project spec.

## Out of scope for this plan

- Populating `TicketSpec` for non-feature work types (bugfix, chore). Their workflows don't use spec context.
- v2 Coordinator path: `coordinator.materialize_layer()` calls `tickets.create()` directly (not
  `handle_create_ticket`), so spec materialisation won't fire for v2-build-plan tickets. The v2 epic schema
  maps to architecture modules, not capabilities, so wiring this needs separate design work — tracked as
  follow-on.
- Deleting the `spec` role YAML file. It stays in case projects reference it in custom workflows.
- Migrating any existing eval projects' specs — they regenerate on the next run.

## Change log

- 2026-05-24: Initial draft (brent)
