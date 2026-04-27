---
title: Project Spec Schema — Design
type: design
status: draft
owner: brent
created: 2026-04-27
updated: 2026-04-27
problem: ./problem.md
---

# Project Spec Schema — Design

## Summary

A formal Pydantic-validated schema for `project.structured.yaml`, a brief-format extension that carries stable IDs and structured behavior/AC blocks, a regeneration algorithm that preserves operator-owned metadata, a `project://spec/...` URI resolver, and a capability-aware MCP tool surface. The init workflow gains a brief-approval step between PO finishing and spec-gen running. Humans only ever edit the brief; spec-gen owns the structured form.

## Approach

Five new pieces, three modified pieces:

**New:**

1. `jig/spec_schema.py` — Pydantic models (`StructuredSpec`, `Capability`, `Behavior`, `NonGoal`, `UserStory`, `CapabilityState`).
2. Brief parser (lives in `jig/init_mcp.py` or new `jig/brief_parser.py`) — markdown with `{#id}` anchors → intermediate `BriefCapability` / `BriefNonGoal` form.
3. Regeneration merge (in `jig/init_mcp.py` and the spec-gen agent) — match by ID + alias, preserve metadata, surface gaps.
4. URI resolver branch in `jig/context_resolver.py` — `project://spec/...` → structured/text.
5. Capability-aware MCP tools in `jig/mcp_server.py`.

**Modified:**

6. `handle_spec_publish` validates against `StructuredSpec`. `handle_spec_load_existing` returns the current spec (or empty) for spec-gen merge.
7. `init_workflow` gets `BRIEF_APPROVAL` ResumeState + handler.
8. PO / spec-generator / SA role configs and prompts updated.
9. `jig.ticket.Ticket` gains `derived_from: str | None = None` for ticket→capability linkage.

The split is: schema + URI scheme are pure-data and pure-Python pieces; brief parser + regen + MCP tools are the workflow integration; role updates close the loop with the agents.

## Interfaces

### Brief format (Markdown)

The brief is the single source of truth for content AND IDs. Extensions over today's loose format:

**Anchor syntax — definitions vs references:**
- `{#slug}` **defines** an ID at this location (heading or bullet).
- `[slug]` **references** an existing ID elsewhere (used for AC bullets pointing at their behavior).

**Sections** (in order):

```markdown
# <project name>

<intro paragraph — becomes StructuredSpec.summary>

## Built

### <Capability title> {#capability-id}

<prose summary>

**User story:** (optional)
As a <persona>, I want <capability> so that <benefit>.

**Behaviors:**
- {#behavior-id} <one-line description>

**Acceptance criteria:**
- [behavior-id] <testable condition>

## Planned (committed)

(same elaborated structure as Built)

## Planned (not yet committed)

- {#capability-id} <one-line title and description>

## Backlog

- {#capability-id} <one-line idea>

## Archived

(same elaborated structure as Built; for capabilities once Built, now retired)

## Non-goals

- {#non-goal-id} <text> — <rationale>
```

**Aliases** in anchors for ID renames:

```markdown
### Deadlines {#deadlines aliases:due-dates}
```

`aliases:` is a comma-separated list of prior IDs. Spec-gen uses these to match the brief item to an existing capability whose `id` was previously the listed alias.

**Section → state mapping:**

| Brief section | Capability `state` |
|---|---|
| `## Built` | `built` |
| `## Planned (committed)` | `planned` (or `in_progress` if a ticket is open against the ID — spec-gen detects from ticket store) |
| `## Planned (not yet committed)` | `planned` (with no behaviors elaborated) |
| `## Backlog` | `backlog` |
| `## Archived` | `archived` |
| `## Non-goals` | not capabilities — go to `non_goals` |

**Format rules** (each is a blocking gap if violated):
- Every `### <title>` heading has `{#slug}`.
- Every bullet under Planned-not-committed / Backlog / Non-goals has leading `{#slug}`.
- Every behavior bullet has `{#behavior-id}`. Every AC bullet has `[behavior-id]`.
- AC `[behavior-id]` references resolve to a behavior in the same capability.
- IDs unique across the whole brief (capabilities, non-goals, behaviors namespaced per capability).
- Aliases unique across the brief, no collision with any `id`.
- Capability under Planned (committed) / Built has either elaborated behaviors with AC (≥1 each), or capability-level AC if no behaviors.
- Backlog items have no `**Behaviors:**` or `**Acceptance criteria:**` blocks.

### URI scheme

| URI | Resolves to |
|---|---|
| `project://spec` | The whole `StructuredSpec` |
| `project://spec/name` | The `name` field |
| `project://spec/summary` | The `summary` field |
| `project://spec/capabilities` | The full list of capabilities |
| `project://spec/capabilities/<id>` | One Capability by ID (or alias) |
| `project://spec/capabilities/<id>#<behavior-id>` | One Behavior within that capability |
| `project://spec/non-goals` | The full list of non-goals |
| `project://spec/non-goals/<id>` | One NonGoal by ID (or alias) |
| `project://spec/state/<state-name>` | All capabilities with that state |

Resolution returns rendered text for context-bundle injection; structured data via `spec_resolve_uri()` MCP tool. Unknown IDs raise (loud failure).

### MCP tool surface

**Generic** (kept from today, used by SA fallback / debugging):
- `spec_list_fields()` — flat dot-paths
- `spec_get_field(path)` — value at dot-path

**Capability-aware** (used by SA, ticket-spec, dev, PM):
- `spec_list_capabilities(state: str | None = None)` → `[{id, title, state}]`
- `spec_get_capability(id: str)` → full Capability (alias-resolved)
- `spec_get_behavior(capability_id: str, behavior_id: str)` → full Behavior
- `spec_list_non_goals()` → `[{id, text, rationale}]`
- `spec_get_non_goal(id: str)` → full NonGoal (alias-resolved)
- `spec_resolve_uri(uri: str)` → `{kind, data}` for any `project://spec/...`

**Spec-generator-only** (regen support):
- `spec_load_existing()` → current StructuredSpec dict, or `{}` if no spec yet
- `spec_publish(yaml_content, advisory_notes)` — existing, now Pydantic-validated
- `spec_report_gaps(gaps)` — existing

### Init workflow

New `ResumeState.BRIEF_APPROVAL` between `PO_CONVERSATION` and `SPEC_GENERATION`:

```
PO_CONVERSATION
   │  PO calls po_finish_brief
   ▼
BRIEF_APPROVAL          ← NEW
   │  Operator [Y]es
   ▼
SPEC_GENERATION
   ├──→  spec_publish (no gaps)        → BRANCH_PROMPT
   ├──→  spec_publish (advisory)       → BRANCH_PROMPT (advisory shown)
   └──→  spec_report_gaps (blocking)   → GAP_PROMPT → PO_CONVERSATION
```

`prompt_brief_approval` renders the brief markdown and offers `[Y]es / [r]esume PO / [n]o`. Approval recorded as `brief_approved` SystemEvent on the brief ticket. `classify_resume` returns `BRIEF_APPROVAL` when `last_handoff_idx > last_brief_approved_idx` and `spec_generated` not yet posted.

## Data model

### `jig/spec_schema.py`

```python
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class CapabilityState(str, Enum):
    BACKLOG = "backlog"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BUILT = "built"
    ARCHIVED = "archived"


class UserStory(BaseModel):
    """Optional WHO + WHAT + WHY framing for a capability."""
    as_: str = Field(alias="as")
    want: str
    benefit: str


class Behavior(BaseModel):
    id: str                                  # stable, slugged, unique within capability
    description: str                         # one-line "what it does"
    examples: list[str] = []                 # illustrative, optional
    acceptance_criteria: list[str] = Field(min_length=1)


class NonGoal(BaseModel):
    id: str
    text: str
    rationale: str = ""
    aliases: list[str] = []


class Capability(BaseModel):
    id: str
    title: str
    state: CapabilityState
    summary: str = ""
    user_story: UserStory | None = None
    behaviors: list[Behavior] = []
    acceptance_criteria: list[str] = []      # capability-level AC; used when no behaviors
    excluded: list[str] = []                 # capability-level non-goals
    open_questions: list[str] = []
    tickets: list[str] = []                  # rebuilt by spec-gen from ticket store
    aliases: list[str] = []
    created_at: datetime
    last_updated: datetime
    state_changed_at: datetime

    # Cross-field validator: state ∈ {planned, in_progress, built} requires
    # AC somewhere — capability-level OR every behavior has its own.


class StructuredSpec(BaseModel):
    name: str
    summary: str
    capabilities: list[Capability] = []
    non_goals: list[NonGoal] = []
    generated_at: datetime
    spec_version: int = 1
```

**Notes:**
- `capabilities` is a list (not a dict-by-id) to preserve brief ordering. Lookups via helper.
- `tickets` denormalized from Ticket records — see `Ticket model change` below.
- `aliases` on both Capability and NonGoal — same identity discipline.
- `spec_version` for forward-compat; bumped on breaking schema changes.
- Backlog/archived capabilities omit behaviors entirely (brief format rule), so the unconditional `Behavior.acceptance_criteria` min-length-1 constraint never fires for them — Pydantic-level and state-aware rules don't conflict.

### Ticket model change

`jig.ticket.Ticket` gains a new field:

```python
derived_from: str | None = None  # e.g. "project://spec/capabilities/due-dates"
```

This is the source of truth for ticket-to-capability linkage. Set by whoever creates the ticket (PM during ticket creation, init's PO during architecture-phase ticket scaffolding, etc.). Spec-gen reads it during regen Phase 4 to rebuild `Capability.tickets`.

For v1 init, no tickets exist yet at spec-gen time, so `Capability.tickets` is always empty. The field is wired now so that subsequent ticket creation (post-init, in PM/dev workflows) can populate the linkage without revisiting the spec schema.

### Validation rules

**Schema-level (Pydantic):**
- Field types match.
- `Behavior.acceptance_criteria` ≥ 1 item.
- `CapabilityState` is one of the 5 enum values.
- `id`, slugs match `^[a-z0-9][a-z0-9-]*$`.
- `spec_version` matches the supported value.

**Model-level (cross-field):**
- AC requirement by state: `planned | in_progress | built` ⇒ AC exists somewhere (capability or all behaviors). `backlog | archived` ⇒ AC optional.

**Brief-format-level (in spec-gen, before building Pydantic models):**
- (See "Format rules" in the brief format section.) All blocking.

**Semantic-level (in spec-gen, post-parse):**
- Non-goal text contradicts a planned/built capability title — advisory.
- Capability marked `built` has no open ticket — advisory.
- Capability with no description and no behaviors AND state ≠ backlog — advisory.
- Capability removed from brief but present in existing structured.yaml — blocking.
- ID rename detected (alias resolves to existing capability) but multiple matches found — blocking ("ambiguous alias").

All gaps go through `spec_report_gaps`. Blocking gaps prevent publish; advisory gaps attach as Notes alongside `spec_publish`.

### Regeneration algorithm

```
Phase 1: Parse the brief
─────────────────────────
Parse markdown into:
  briefcaps: list[BriefCapability]
  briefnogoals: list[BriefNonGoal]

Validate brief format rules. Any failure → spec_report_gaps blocking.

Phase 2: Load existing structured spec
──────────────────────────────────────
existing = spec_load_existing()  # or {} if first-time

Phase 3: Match brief items
──────────────────────────
For each capability in briefcaps:
  # Try direct ID match first; then any of brief's aliases against
  # existing's id-or-aliases. The second path catches the rename case
  # (operator changed the brief's {#id} and listed the old id in aliases).
  match = existing.capability_by_id_or_alias(brief_cap.id) or
          existing.capability_by_alias_in(brief_cap.aliases)
  if match:
    # Brief is source of truth for id + aliases. If brief renamed (id
    # differs from match.id), match.id picks up the new value; the old
    # id only survives if the operator also added it to brief_cap.aliases.
    preserve: created_at, state_changed_at, tickets
    overwrite from brief: id, aliases, title, summary, user_story,
                          behaviors, AC, excluded, open_questions
    bump last_updated = now
    if brief_cap.state != match.state:
      match.state = brief_cap.state
      match.state_changed_at = now
  else:
    new — created_at = now, all timestamps = now

Same merge for non-goals.

For each existing capability NOT matched in brief:
  → blocking gap "removed from brief — add back, alias to another, or move to ## Archived"

Phase 4: Rebuild ticket linkage
───────────────────────────────
For each capability:
  capability.tickets = [
    t.id for t in ticket_store
    if t.derived_from == f"project://spec/capabilities/{capability.id}"
       or t.derived_from in {f"project://spec/capabilities/{a}" for a in capability.aliases}
  ]

Phase 5: Validate & write
─────────────────────────
StructuredSpec.model_validate(merged)
atomic_write_text(.jig/spec/project.structured.yaml, yaml.safe_dump(...))
post SystemEvent("spec_generated") on brief ticket
resolve brief ticket
```

**Preserved across regen:** `id`, `aliases`, `created_at`, `state_changed_at`, `tickets` (denormalized).

**Recomputed every regen:** `title`, `summary`, `user_story`, `description`, `behaviors[*]`, `acceptance_criteria`, `excluded`, `open_questions`, `last_updated`, `generated_at`.

**Spec-gen never modifies:** the brief, ticket records.

## Alternatives considered

### Auto-slug IDs from titles, hidden from operators

Capability IDs computed as `slug(title)` automatically; operators never see them. Simplest schema change (no brief format extension). **Rejected** because title renames break URIs — every decision record and ticket link that referenced the old slug becomes a dangling pointer. Aliases would require fuzzy matching or operator confirmation flows. The brief-carries-IDs approach (chosen) makes operators slightly more aware but eliminates the rename ambiguity.

### Hidden UUID IDs with separate slugs for display

Each capability has both a UUID (immutable forever) and a slug (display-only, regenerated from title). URIs use UUIDs. **Rejected** because URIs become opaque (`project://spec/capabilities/8f2c…`) — bad for human reading, decision records, debugging, log lines.

### Spec-gen writes IDs back to the brief on first run

PO writes the brief without IDs the first time; spec-gen back-fills `{#slug}` anchors on first generation. Friendlier first-time UX. **Rejected** because it crosses the "spec-gen never writes the brief" boundary, and our flow is always PO-mediated anyway — telling PO to add anchors is a one-line prompt addition. Cleaner ownership story.

### Behaviors deferred to ticket-spec phase

Capability stays a flat `description: str`; behaviors and AC live only on ticket specs. **Rejected** because SA needs concrete units to reason about ("does this capability need a websocket?"), and ticket specs come too late — we'd be making architecture choices against vague prose. Behaviors at the project-spec level are the right granularity for SA, and they tie cleanly to ticket specs (each ticket implements one or more behaviors).

### Given/When/Then for AC

AC structured as `{given, when, then}` triples (BDD style). **Rejected** per the existing 02-project-spec doc's caution: "BDD/Gherkin as false contracts… warps for continuous, visual, performance-related behaviors." Flat strings are testable enough.

### Humans edit structured.yaml directly

Operators handle ID renames, alias declarations, and archival by editing the structured form. **Rejected** because it doubles the surface humans have to know (brief format AND structured-yaml conventions), and any divergence between the two becomes a real drift problem. Brief-only editing keeps the contract crisp.

### Auto-archive removed-from-brief capabilities

Spec-gen silently moves removed capabilities to `state: archived` rather than gapping. Less friction. **Rejected** because removing a capability from the brief is often a typo or accidental rewrite; auto-archiving silently drops things the operator might not have meant to retire. Blocking gap forces explicit acknowledgment.

### Chosen: schema + brief-format extension + spec-gen-only structured edits

Picks the boundaries that keep human authorship in one place (the brief) and machine generation in another (`spec-gen` → structured.yaml). Brief carries IDs explicitly so regeneration is unambiguous. Capability content is structured enough (behaviors with AC) that downstream agents can query at the domain level, not the YAML-path level. The schema's defaults and optional fields keep simple cases simple ("change icon to blue" needs no behaviors, just capability-level AC).

## Risks

- **Brief format complexity for the PO agent.** PO has to learn anchor syntax, behavior blocks, AC blocks, AC-references-behavior syntax. If PO produces invalid briefs, the gap loop fires every time. **Mitigation:** PO prompt gets a worked example; spec-gen's gap reports are operator-readable so PO can reason about them when looped back.
- **Spec-gen's job grows** — markdown parsing with anchors, validation, regen merge, `spec_load_existing` integration. **Mitigation:** `spec_load_existing` returns `{}` for first-time so the simple case is short; spec-gen prompt walks through the algorithm step-by-step; each phase has its own gap kinds for traceable failures.
- **AC mandatory coupling.** PO has to ask for AC at elaboration time — every behavior — or the brief fails validation. Slows the PO conversation. **Mitigation:** PO prompt is explicit about asking for AC right when the operator describes a behavior. Operator can give one-liners; PO doesn't need elaborate AC.
- **Regeneration determinism.** If spec-gen produces non-deterministic output (different ordering, different ID generation timing), repeated runs without brief changes will produce diff churn. **Mitigation:** spec-gen reads brief order and preserves it; timestamps only bump when content actually changed.
- **Ticket linkage is denormalized** — `Capability.tickets` must be rebuilt every regen. If a ticket is created with `derived_from` pointing at an alias and the alias is removed, the linkage breaks silently. **Mitigation:** spec-gen rebuilds from the ticket store every run; alias removal is itself a brief edit, so the operator sees the change explicitly.
- **Format-rule violations are rejected loudly.** A small typo in an anchor (`{# missing-space}`, `{#duplicate-id}`) blocks the whole spec. **Mitigation:** acceptable trade — alternative is silent corruption; gap reports identify the exact line/anchor.

## Out of scope

- `jig spec migrate-v1` CLI for prototype-era spec migration. `--force` reset is the dev workflow.
- Hard-deletion of capabilities via the brief. Removed = blocking gap; archive is the way to retire. Hard-delete is a future CLI command.
- Tests-to-AC mapping. AC stay flat strings; future schema bump (spec_version: 2) can introduce AC objects with their own IDs without breaking the `{#id}` / `[id]` syntax distinction.
- BDD-style structured AC (Given/When/Then).
- Capability dependencies / blocks-graph.
- Sub-element addressing within a capability beyond Behaviors (AC fragments, etc.).
- Diff/history tooling for the spec — git provides this.
- Multiple project specs per repo (monorepo support). Doc 02 already deferred this.

## Open questions

(None — all resolved during brainstorming. Decisions:)

- **Brief parser:** hand-rolled walker. Switch to a library (`marko` / `markdown-it-py`) only if we hit real problems.
- **`spec_resolve_uri` URI form:** strict — full `project://spec/...` prefix required, no relative URIs.

## Change log

- 2026-04-27: Initial draft (brent)
