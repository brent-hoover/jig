---
title: SA as Architect — Design
type: design
status: active
owner: Brent Hoover
created: 2026-06-07
updated: 2026-06-07
problem: ./problem.md
---

# SA as Architect — Design

## Summary

Two-phase delivery. **Phase 1** (pending egress verification — see Open questions): add research tools (Context7, WebFetch, WebSearch),
decision rules, and a validated `TechDecision` model to the v1 `sa.yaml` that both profiles
currently use. SA grounds each framework and package choice in a current source, and follows
explicit prompt rules to make reproducible choices — ambiguous dimensions become `open_questions`
rather than silent picks. **Phase 2** (after #137 and PO topology decision): merge `sa.yaml` and
`sa_mvp.yaml` into one size-adaptive role that uses the appropriate tool set and spec topology
for S/M/L projects, and adds `BoundariesFile` + semgrep enforcement for module isolation.

This design supersedes `feature-work/architecture-skeleton/design.md` (absorbed into Phase 2).

## Current state

- `small.yaml` and `medium.yaml` both have `sa_role: sa` — v1 `sa.yaml` runs for all projects
- `sa_mvp.yaml` (`role: sa-mvp`) implements the v2 SA path but cannot activate: (a) `classify_resume`
  only handles `sa_propose_scaffold`, not `arch_finalize` (#137, in progress); (b) medium PO
  currently produces flat `project.structured.yaml`, but `sa_mvp` expects L0–L3 artifacts
  (`discovery.md`, `suites.yaml`, per-suite `spec.structured.yaml`) that no profile produces yet
- `sa_mvp.yaml` is referenced in: `test_sa_incremental_registration.py`,
  `test_renderers_pydantic_from_data_contract.py`, `test_load_role_by_role_id.py`,
  `schemas/profile.py:40`, `tui/screens/now.py:126,149,179` — not safe to delete
- `sa_v2.yaml` is referenced in active bones simulation scenarios — not touched by this design
- Both tool sets (`sa_propose_scaffold` from `init_mcp.py`; `arch_set_module`/`arch_finalize`
  from `sa_incremental_mcp.py`) are registered in the same `mcp_server.py`; `allowed_tools`
  in the role file is the only gate

---

## Phase 1 — Research tools + TechDecision (pending egress verification)

### 1. `TechDecision` schema addition (`jig/schemas/arch.py`)

```python
class SourceType(str, Enum):
    context7           = "context7"
    live_fetch         = "live_fetch"
    operator_specified = "operator_specified"
    inferred           = "inferred"   # training-data only; no external source consulted

class TechDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str                         # kebab-case, e.g. "cli-framework"
    choice: str                     # e.g. "typer"
    rationale: str
    source_type: SourceType
    source_ref: str | None = None   # Context7 library ID, URL, or None
    version_pinned: str | None = None
```

Add to `Architecture`:
```python
tech_decisions: list[TechDecision] = Field(default_factory=list)
```

Default empty; backwards compatible; existing v2 `architecture.yaml` files (conformant
`Architecture` YAMLs produced by the sa_mvp path) parse cleanly.

**Phase 1 validation note**: the init-phase `architecture.yaml` produced by `apply_scaffold`
is a free-form dict (keys: `template`, `language`, `decisions`, etc.) and is NOT run through
`Architecture.model_validate` — `Architecture` has `extra="forbid"` and would reject those
keys. In Phase 1, `tech_decisions` entries are validated standalone against `TechDecision` at
the `handle_sa_propose_scaffold` handler boundary. The `Architecture` field addition is purely
forward-positioning so Phase 2 (which does write conformant `Architecture` YAMLs) doesn't
require another schema change.

### 2. SA research tools and size-selection prompt

`sa.yaml` gains:
- `context7` in `allowed_mcps`
- `WebFetch` and `WebSearch` in `allowed_tools`
- `strict_tools: false`
- Rewrite line 20 (the tool-enumeration gate: "these are the only ones you have") to reflect
  the expanded tool set; lines 57–58 prohibit Bash/Glob/Grep/Edit/Write and need no change

**Size-selection prefix** — SA reads the spec, selects S/M/L, and states the choice with
reasoning in the first thread message. Operator can override during the confirmation prompt
(size override capture/persistence deferred to Phase 2 — Phase 1 renders size as read-only).

| Size | Trigger | Architecture output |
|------|---------|---------------------|
| S | 1–2 capabilities, no external APIs | free-form arch.yaml + `tech_decisions` |
| M | 3–6 capabilities or any external API | same as S; Phase 2 adds modules/contracts/boundaries |
| L | 7+ capabilities or explicit module isolation | Phase 2 |

Precedence rule: any external API → M minimum, regardless of capability count.

**Research protocol**:
1. Resolve each library/service in Context7 (`resolve-library-id`); query current docs
2. Record `source_type: context7`, `source_ref: <library-id>`, `version_pinned`
3. Fallback: WebFetch the official docs page → `source_type: live_fetch`
4. Operator-stated preference → `source_type: operator_specified`
5. Last resort → `source_type: inferred`

For external APIs (any size): live-fetch a representative unauthenticated endpoint; record
actual response shape as a `TechDecision` with `source_type: live_fetch`. Authenticated APIs:
`source_type: inferred`, emit an `open_question`.

**Reproducibility decision rules** — for the dimensions that drove the original failure:
- `async_io`: async if spec contains "streaming", "concurrent", "real-time", or "WebSocket";
  sync otherwise; genuinely ambiguous → `open_question`
- `cli_framework`: typer for typed-arg subcommand interfaces; click for raw flexible parsing;
  both valid → Context7 compare (recency/maintenance signal), still tied → `open_question`
- `http_client`: httpx for Python (async/sync dual support) unless spec provides a driver for
  an alternative

Two runs against the same unambiguous spec + the same decision rules → same committed
`tech_decisions`. Ambiguous dimensions surface as `open_questions`.

**Proportionality**:
- S → verify package versions; no live fetches
- M → full research per decision; live-fetch each external API endpoint

### 3. `sa_propose_scaffold` extension

Three changes in concert:

**`jig/mcp_server.py` — `sa_propose_scaffold` tool registration (~line 2750)**: Add
`tech_decisions: list` and `size: str` to the `@tool` param schema dict and update the wrapper
to extract and forward them. Without this, the SA agent cannot include the new params and the
handler additions are unreachable. Also update the tool description string.

**`jig/init_mcp.py` — `handle_sa_propose_scaffold` (~line 443)**: gains
`tech_decisions: list[dict] = []` (validated against `TechDecision` on receipt) and
`size: Literal["S", "M", "L"] = "S"` (logged in the thread payload, shown in confirmation).
`constraints` and `open_questions` already exist. `decisions`/`config` deprecated but still
written during Phase 1 (so `_scaffold_summary_for_pm`'s existing readers don't regress while
the migration completes). `tech_decisions` is a new sibling key alongside `decisions`.

**`jig/init_workflow.py` — `apply_scaffold` (~line 1566)**: gains `tech_decisions: list[dict] = []`.
Passed only from the SA-accept call site (line 567, `sa_path=True`). The direct-template-pick
call site (line 586, `sa_path=False`) passes no `tech_decisions`; the param defaults to `[]`
and the SA-path guard at ~line 1606 prevents any write. `apply_scaffold` writes `tech_decisions`
as a new key in the architecture.yaml dict, guarded by `if tech_decisions:` (matching the
existing `decisions`/`constraints` guard pattern).

`_scaffold_summary_for_pm` (~line 635) is updated to also read the new `tech_decisions` key
from architecture.yaml and append a grounding-source summary, while keeping the existing
`decisions` read for backwards compat in Phase 1.

### 4. Operator confirmation

`render_sa_confirm_prompt` in `jig/init_workflow.py` (~line 1193) gains `tech_decisions`.
Confirmation prompt renders:
- Project size selection + SA reasoning
- Table: ID | choice | source_type | source_ref

Both call sites (CLI via `init_prompts.py:111` and TUI via `tui_prompts.py`) updated.

### 5. Planning-ticket gate

`_create_planning_ticket` (~line 602) calls `_scaffold_summary_for_pm` to build its ticket
description. `_scaffold_summary_for_pm` now reads `tech_decisions` from the persisted
`architecture.yaml` (alongside `decisions`) and checks for any entry with
`source_type: inferred`. If any exist, it returns a warning section listing the ungrounded
decision IDs; `_create_planning_ticket` includes this in the ticket description. Soft gate —
warns but does not block.

Justification for soft gate: blocking on `inferred` would halt init for projects with
authenticated APIs (where live-fetch is impossible). The operator confirmation prompt is the
right enforcement point for blocking cases.

### 6. Dev and test agent access

Add `arch_get_field` to `allowed_tools` for `dev.yaml` and `test.yaml` (already present in
`sa.yaml`). PM gets `tech_decisions` via the planning-ticket description.

---

## Phase 2 — SA unification + boundaries (after #137 + PO topology decision)

### Overview

Once `classify_resume` handles `arch_finalize` (#137) and the M-size PO topology is defined
(does medium get L0–L3 spec artifacts, or does `sa_mvp` adapt to flat spec?), the two SA paths
merge into a single `sa.yaml` with S and M/L sections.

`sa_mvp.yaml` is **not deleted** — it is retained as a deprecated alias and its test coverage
updated. The ~250-line `sa_mvp` prompt (discovery loop, SA checklist, behavioral contracts,
spike/cascade workflow) is absorbed into the M/L section of `sa.yaml`. The existing
`arch_regenerate_pydantic_models` tool is included in the M/L tool list.

**Preconditions for Phase 2:**
- #137 landed (classify_resume handles arch_finalize)
- PO topology for medium decided: either (a) medium PO gains L0–L3 agents producing
  `discovery.md`/`suites.yaml`, or (b) `sa_mvp` prompt is refactored to accept flat spec
- `sa_mvp` deletion blast radius enumerated and resolved (`profile.py:40`, `now.py:126,149,179`,
  `test_load_role_by_role_id.py:36`, `test_renderers_pydantic_from_data_contract.py:441`)

### `BoundariesFile` schema and `sa_write_boundaries` tool

```python
class OntologyTerm(BaseModel):
    term: str
    definition: str

class InternalBoundaries(BaseModel):
    allowed_modules: list[str] = Field(default_factory=list)
    forbidden_modules: list[str] = Field(default_factory=list)
    rationale: str | None = None

class ExternalBoundaries(BaseModel):
    allowed: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    rationale: str | None = None

class BoundariesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_version: int = 1
    module: str
    ontology: list[OntologyTerm] = Field(default_factory=list)
    internal: InternalBoundaries = Field(default_factory=InternalBoundaries)
    external: ExternalBoundaries = Field(default_factory=ExternalBoundaries)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)
```

`sa_write_boundaries` (parallel to `sa_write_contracts`) validates and writes
`.jig/spec/modules/<m>/boundaries.yaml`.

### Semgrep rule generation

`generate_boundary_rules(project_path: Path)` runs at end of `arch_finalize`. Reads every
`modules/<m>/boundaries.yaml`; writes per-module semgrep rule files to
`.jig/enforcement/semgrep/boundaries/<m>.yaml`. Generation is idempotent.

The dev agent phase gate gains a semgrep step when
`.jig/enforcement/semgrep/boundaries/` is non-empty. Violations surface to the agent as a
gate failure. Semgrep unavailable → warn-only (never silent).

### Open question (blocking Phase 2)

**Package path derivation**: how does `generate_boundary_rules` map module id `job-posting` to
Python package `ats.job_posting`? Options: (a) `package_prefix` in `project.yaml`, (b)
explicit `package_path` field on `Module`, (c) kebab → snake + project name convention.
Decision required before `BoundariesFile` schema finalises.

---

## Interfaces

**Phase 1:**
- `sa_propose_scaffold` gains `tech_decisions: list[dict]`, `size: str`
- `Architecture` gains `tech_decisions: list[TechDecision] = []`
- `apply_scaffold` gains `tech_decisions: list[dict]` parameter
- `arch_get_field("tech_decisions")` available to dev and test

**Phase 2 (additional):**
- `sa_write_boundaries` new MCP tool
- `BoundariesFile` new schema
- Unified `sa.yaml` absorbs `sa_mvp.yaml` M/L behavior

## Data model

```yaml
# .jig/spec/architecture.yaml — after Phase 1 (free-form + new key)
template: python-cli
language: python
constraints:
  - HTTP client must be injectable for test fixture replay
tech_decisions:
  - id: cli-framework
    choice: typer
    rationale: Declarative subcommand + flag parsing, confirmed via Context7.
    source_type: context7
    source_ref: /typer/latest
    version_pinned: "0.12"
  - id: hn-api-item-shape
    choice: "type always 'story'; Ask HN/Show HN via title prefix only"
    rationale: Live fetch confirmed; brief vocabulary incorrect.
    source_type: live_fetch
    source_ref: https://hacker-news.firebaseio.com/v0/item/8863.json

# .jig/spec/modules/job-posting/boundaries.yaml — Phase 2 only
spec_version: 1
module: job-posting
ontology:
  - term: Candidate
    definition: A person who has submitted an application for this posting.
internal:
  allowed_modules: [candidate, shared-types]
  forbidden_modules: [billing, auth]
external:
  allowed: [httpx, pydantic]
  forbidden: [requests]
```

## Alternatives considered

### Simplest — research tools + prompt update only, no schema change

Add Context7/WebFetch to `sa.yaml`; add decision rules; record decisions in the existing
free-form `decisions` dict informally.

*Drawbacks:* No schema validation on tech decisions — source_type is advisory, not enforced.
Unresolvable ambiguous cases have no structured open_question path. Phase 2 requires a schema
change that could have been done now at zero risk (it's additive).

### Complete — Phase 1 + Phase 2 as designed (chosen)

Validated `TechDecision` with reproducibility rules in Phase 1; SA unification + boundaries
in Phase 2 after blockers clear.

*Drawbacks:* Phase 2 has blockers (#137, PO topology, sa_mvp blast radius). Phase 1 ships
independently; Phase 2 follows when those are resolved.

### Optimal — immediate full unification in one PR

All in one: unified SA, boundaries, semgrep, validated decisions.

*Drawbacks:* Blocked on #137 (in progress), PO topology decision (unresolved), and sa_mvp
blast-radius enumeration. The Phase 2 blockers make immediate unification impractical. Phasing
delivers Phase 1 value without waiting.

### Decision

Complete (phased). Phase 1 is deliverable now and directly closes the reproducibility and
grounding requirements. Phase 2 absorbs the architecture-skeleton scope and the SA unification
once its preconditions are met.

## Risks

**Phase 1:**
- **[Blocking if false]** Init-phase sandbox egress for Context7 MCP and WebFetch — confirm
  by running `resolve-library-id` and `WebFetch` inside a `jig init` session before starting
  Phase 1 implementation.
- `_scaffold_summary_for_pm` reads `decisions` at `init_workflow.py:672` — must update in the
  same commit as `apply_scaffold` or PM gets an empty/wrong tech summary.
- **Reproducibility is prompt-enforced, not structurally guaranteed.** Decision rules reduce
  LLM non-determinism for the named dimensions but cannot eliminate it. Acceptance test: run
  the hn-cli eval twice and compare `async_io`, `cli_framework`, `http_client` decisions.
  Residual divergence is expected to be rare and traceable to a logged `open_question`.
- SA produces `source_type: inferred` despite having research tools — visible via planning-ticket
  warning. Soft gate; operator confirmation is the enforcement point.

**Phase 2:**
- sa_mvp deletion blast radius: `profile.py:40`, `now.py:126,149,179`,
  `test_load_role_by_role_id.py:36`, `test_renderers_pydantic_from_data_contract.py:441` —
  all must be updated or sa_mvp.yaml retained as alias.
- Semgrep package path derivation may produce incorrect rules for non-standard layouts.
- `sa_mvp` prompt absorption (~250 lines) is the dominant Phase 2 work item; discovery loop,
  SA checklist, behavioral contracts, spike/cascade workflows must all be carried over.
- PO topology for medium is unresolved — Phase 2 cannot start until decided.

## Out of scope

- Making S-size init-phase architecture.yaml conformant with `Architecture.model_validate`
- Continuous re-grounding of tech decisions after project start
- Authenticated API research
- Runtime (import hook) boundary enforcement
- Non-Python boundary enforcement
- `sa_v2.yaml` — kept for bones simulation scenarios; not touched

## Open questions

- **[BLOCKING — Phase 1]** Init-phase sandbox network egress for both Context7 (MCP) and
  WebFetch (tool). Resolve before Phase 1 implementation by probing inside a `jig init` session.
- **[BLOCKING — Phase 2]** PO topology for medium: does medium gain L0–L3 PO agents producing
  `discovery.md`/`suites.yaml`, or does the M-size SA section adapt to flat spec? This
  determines whether Phase 2 requires PO changes as a prerequisite.
- **[BLOCKING — Phase 2 boundaries]** Package path derivation for semgrep rule generation:
  `package_prefix` in config, `package_path` on `Module`, or convention. Resolve before
  `BoundariesFile` schema finalises.
- `sa_v2.yaml`: kept for bones simulation scenarios; no changes in Phase 1 or Phase 2.

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
- 2026-06-07: Revised ×6 — correct SA selection mechanism; v1-only scope; factual corrections;
  merged architecture-skeleton scope; reproducibility mechanism; phased delivery; mark size override
  as Phase 2 (deferred from Phase 1)
