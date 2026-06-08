---
title: SA as Architect — Phase 1 Implementation Plan
type: plan
status: draft
owner: brent-hoover
created: 2026-06-07
updated: 2026-06-08
design: ./design.md
---

# SA as Architect — Phase 1 Implementation Plan

## Overview

Six implementation steps plus one full-verification step, each independently testable. Schema goes first —
purely additive, zero caller impact. Step 2 updates the SA role (prompt + tools) and doubles as the egress
gate: the manual verify at the end of Step 2 confirms whether the init sandbox allows Context7 and WebFetch;
Steps 3–7 are held until that probe passes.
Steps 3 and 4 extend the handoff pipeline: MCP tool registration and handler together (Step 3), then
`apply_scaffold` plus its summary reader in the same commit (Step 4) — these two must land atomically because
the reader (`_scaffold_summary_for_pm`) and the writer (`apply_scaffold`) must update together or PM gets an
empty tech summary. Step 5 threads `tech_decisions` and `size` from the stored proposal through the
confirm-prompt call chain (two render/threading functions + four `ask_sa_confirm` definitions). Step 6
adds dev/test role access. Step 7 is full verification.

Phase 1 does **not** make the init-phase `architecture.yaml` conformant with `Architecture.model_validate` —
`Architecture` has `extra="forbid"` and the free-form init-phase keys (`template`, `decisions`, etc.) would be
rejected. `tech_decisions` is written as a new plain key inside the existing `if sa_path:` block. The
`Architecture.tech_decisions` field added in Step 1 is forward-positioning for Phase 2, not used in Phase 1
validation.

Each step is a standalone commit; reverting a step restores prior behaviour since all changes are additive and
guarded by existing `if sa_path:` / `if tech_decisions:` checks.

## Preconditions

- [x] Design approved (`design.md`).
- [ ] **BLOCKING (Steps 3–7)**: Sandbox egress confirmed for Context7 MCP and `WebFetch` inside a `jig init`
  session. Steps 1–2 are unblocked (schema + role YAML only, no egress needed). Pre-flight before Step 1:
  run `jig start --no-docker` with any ticket and confirm a dev agent can call `resolve-library-id` without
  error — this validates the tool-wiring mechanism using dev.yaml's existing `context7` (dev.yaml:37-41), not
  the init path specifically. The init-path probe requires the Step 2 role change and runs as Step 2's Verify
  gate. If the Step 2 probe fails: revert the `sa.yaml` commit (Step 1 schema changes are safe to keep); the
  SA role is restored to its prior state while the init egress configuration is investigated.
- [x] Work on worktree under `.worktrees/`, not `develop`.

## Out of scope

Per design.md Out of scope, plus Phase 1 additions:
- Phase 2 boundaries/semgrep, SA unification, `Architecture` conformance for init-phase files.
- Authenticated API research.
- `sa_v2.yaml`.
- **Operator size override at the confirm prompt**: design.md line 89 mentions the operator can override S/M/L
  during confirmation, but capturing and persisting that override requires additional state threading. Deferred
  to Phase 2 (or a separate ticket). Phase 1 renders size as read-only information.

## Progress

- [x] Step 1: `TechDecision` schema addition
- [x] Step 2: SA role update (code done; **egress gate still pending**)
- [x] Step 3: `sa_propose_scaffold` MCP extension (folded with Step 2 — prompt + tool must agree)
- [x] Step 4: `apply_scaffold` + `_scaffold_summary_for_pm`
- [ ] Step 5: Operator confirmation call chain
- [ ] Step 6: Dev/test role access
- [ ] Step 7: Full verification

## Steps

### 1. `TechDecision` schema addition (`jig/schemas/arch.py`)

**What:**
- Add `SourceType` enum: `context7 | live_fetch | operator_specified | inferred`
- Add `TechDecision` model: `id` (kebab-case, validated via `validate_kebab_id`), `choice`, `rationale`,
  `source_type: SourceType`, `source_ref: str | None = None`, `version_pinned: str | None = None` —
  with `extra="forbid"` and a `@field_validator("id")` matching the pattern in other `arch.py` models
- Add `tech_decisions: list[TechDecision] = Field(default_factory=list)` to `Architecture`
- Add `SourceType` and `TechDecision` to the `__all__` list (lines 45–71) so they're importable via
  `from jig.schemas.arch import *`

**Why:** Foundation for everything downstream. Additive — `tech_decisions` defaults to empty and `Architecture`
parses without it. Existing conformant `architecture.yaml` files (v2 sa_mvp path) are unaffected.

**Verify:**
- `uv run pytest tests/test_schemas_arch.py -q` — existing arch schema tests pass.
- Add new cases to `tests/test_schemas_arch.py` (existing file, do not create a new one):
  - `TechDecision` validates correctly for all four `SourceType` values.
  - `TechDecision` rejects an unknown field (`extra="forbid"`).
  - `TechDecision` rejects a non-kebab `id` (e.g. `"CLI Framework"`) — enforces the `validate_kebab_id` validator.
  - `Architecture` parses with no `tech_decisions` key (default `[]`).
  - `Architecture` parses with a valid `tech_decisions` list.

---

### 2. SA role update (`jig/defaults/roles/sa.yaml`)

**What:**
- Add `context7` to `allowed_mcps`
- Add `WebFetch` and `WebSearch` to `allowed_tools` — WebSearch locates library/API URLs that Context7 doesn't
  index before a WebFetch; it is not a separate research step but an optional precursor to `source_type: live_fetch`
- Keep `strict_tools: true` (already set; SA uses strict isolation — adding tools to `allowed_tools` is the
  correct extension mechanism, not weakening the deny list)
- Rewrite the tool-enumeration gate (~line 20: "these are the only tools you have") to reflect expanded set
- Update the `sa_propose_scaffold` signature documentation at lines 45–54 to include `tech_decisions` (list of
  `TechDecision` dicts) and `size` ("S"/"M" in Phase 1; "L" added in Phase 2) — so SA knows the full call
  shape before Step 3 lands. Document only "S"/"M" here: Phase 1 handler rejects "L" with `ValueError`, so
  advertising "L" in the tool description would cause SA to pass a value that always fails.
- Extend `phase_prompt` with:
  - **Size-selection prefix**: SA reads the spec, selects S/M in Phase 1 (rule: external API → M minimum;
    "L" is Phase 2 scope), states choice with reasoning in the first thread message
  - **Research protocol**: (1) resolve in Context7 → `source_type: context7`; (2) WebFetch official docs (use
    WebSearch first if the URL is unknown) → `source_type: live_fetch`; (3) operator-stated →
    `source_type: operator_specified`; (4) training only → `source_type: inferred`
  - **Reproducibility decision rules**: `async_io` from spec keywords; `cli_framework` from interface type
    (typer/click; Context7 compare if tied; still tied → `open_question`); `http_client` defaults to httpx
  - **Proportionality**: S → version-check only; M → full research + live-fetch each external API endpoint

**Why:** SA can research before deciding. All change is prompt + role YAML — no code changes in this step.
Documenting the updated `sa_propose_scaffold` signature now means SA will attempt to pass `tech_decisions` and
`size` once Step 3 lands, with no prompt change required then.

**Verify:**
- Add a test to `tests/test_load_role_by_role_id.py` (or equivalent role-loader test) that loads `sa.yaml`
  via `RoleConfig` and asserts `"context7" in role.allowed_mcps`, `"WebFetch" in role.allowed_tools`,
  `"WebSearch" in role.allowed_tools`, and `role.strict_tools is True`.
- `uv run pytest tests/test_load_role_by_role_id.py -q` — new test passes.
- **EGRESS GATE (blocks Steps 3–7)**: Run `jig init` (with Docker, sandboxed) against the hn-cli brief.
  When SA runs, observe: (a) `resolve-library-id` call succeeds (no timeout / auth error); (b) `WebFetch`
  against `https://hacker-news.firebaseio.com/v0/item/8863.json` returns a 200 JSON response with an `id`
  field. `--no-docker` cannot validate this gate — it skips the sandbox that is the source of the egress
  risk. If either call fails sandboxed, investigate bwrap/Docker network config before Step 3.

---

### 3. `sa_propose_scaffold` extension (MCP layer)

**What:**
Two files, one commit:

- **`jig/mcp_server.py` (~line 2750)** — `sa_propose_scaffold` tool registration: add `tech_decisions: list`
  and `size: str` to the `@tool` param schema dict; update the wrapper to extract and forward them; update the
  tool description string. Without this change the SA agent cannot pass the new params — the handler additions
  below are unreachable.

- **`jig/init_mcp.py` (~line 443)** — `handle_sa_propose_scaffold`: add `tech_decisions: list[dict] | None = None`
  and `size: str = "S"`; normalize `tech_decisions` to `[]` at the top of the function body (avoids mutable
  default pitfall). Validate `size` as `Literal["S", "M"]` inside the handler body (the `@tool` schema
  uses `str`; the `Literal` check must live in the handler — `"L"` is rejected with `ValueError` in Phase 1;
  see design.md Phase 1 L-size constraint). Validate each `tech_decisions` entry
  against `TechDecision` on receipt; raise `ValueError` on schema violation. The existing handler already
  merges `config` and `decisions` into `merged_decisions` and stores it as `decisions` in the payload — do not
  change that logic. Add `tech_decisions` and `size` as new sibling keys in the `Note(payload={...})` dict at
  `init_mcp.py:~473` — this is the same payload that `latest_scaffold_proposal` returns and that Steps 4–5
  consume via `proposal.get("tech_decisions")` / `proposal.get("size")`.

**Why:** Carries grounded decisions from SA through the handoff seam. The tool schema change and the handler
change must land together — a mismatched schema would silently drop the new params.

**Verify:**
- Unit tests for `handle_sa_propose_scaffold`:
  - Valid `tech_decisions` list stored as `tech_decisions` in Note payload.
  - Invalid entry (missing required field) raises `ValueError`.
  - Omitting `tech_decisions` entirely succeeds (backwards compat; stores `[]`).
  - `size = "M"` stored in payload; `size = "X"` raises `ValueError`.

---

### 4. `apply_scaffold` + `_scaffold_summary_for_pm` (`jig/init_workflow.py`)

**What:**
Two functions, one commit (reader and writer must stay in sync):

- **`apply_scaffold` (~line 1566)**: gains `tech_decisions: list[dict] | None = None` and `size: str = "S"`;
  normalize `tech_decisions` to `[]` at the top of the function body. Inside the existing `if sa_path:` block
  (~line 1606), add:
  ```python
  data["size"] = size          # always written when sa_path=True — Phase 2 reads this
  if tech_decisions:
      data["tech_decisions"] = tech_decisions
  ```
  `size` is written unconditionally when `sa_path=True` (not inside `if tech_decisions:`) so Phase 2 can read
  `arch_get_field("size")` even when SA produced no tech_decisions. The SA-accept call site (~line 567) passes
  `tech_decisions=proposal.get("tech_decisions", [])` and `size=proposal.get("size", "S")`. The
  direct-template-pick call site (~line 586, `sa_path=False`) does not pass either; the `if sa_path:` guard
  prevents any write. **Do not** convert the architecture.yaml write to `Architecture.model_dump()` — the
  file remains a free-form dict in Phase 1.

- **`_scaffold_summary_for_pm` (~line 635)** (currently `-> str`; signature unchanged): reads the new
  `tech_decisions` key from `architecture.yaml` via `arch.get("tech_decisions", [])`, appends a formatted
  source summary (choice + source_type per decision) to the returned string, and appends an inferred-decision
  warning section listing the inferred IDs if any entry has `source_type: inferred`. Keep reading `decisions`
  (line 672) for backwards compat. The caller (`_create_planning_ticket` at line 619) uses the returned string
  as-is — no caller change needed.

**Why:** `tech_decisions` is now persisted in the scaffold artifact and surfaced to PM via the planning ticket.
The reader and writer must land in the same commit — if `apply_scaffold` writes `tech_decisions` but
`_scaffold_summary_for_pm` hasn't been updated, PM gets an empty tech summary on the first run.

**Verify:**
- Integration test: `apply_scaffold` with `sa_path=True`, a `tech_decisions` list, and `size="M"`;
  read `architecture.yaml`; assert `tech_decisions` key present and matches input, and `size == "M"`.
- Integration test: `apply_scaffold` with `sa_path=True` and `tech_decisions=[]`; assert `size` key still
  present (written unconditionally) and `tech_decisions` key absent.
- Integration test: `apply_scaffold` with `sa_path=False`; assert neither `tech_decisions` nor `size` key
  is written.
- Seam test (SA-accept path): write a `Note` payload with `tech_decisions` and `size` using the Step 3 handler
  (or by constructing it directly as Step 3 would), read it back via `latest_scaffold_proposal`, then call
  `apply_scaffold` using the SA-accept call site (init_workflow.py:~567) with
  `tech_decisions=proposal.get("tech_decisions", [])` and `size=proposal.get("size", "S")`. Assert both
  `tech_decisions` and `size` appear in `architecture.yaml`.
  This step is inert (returns `[]`) until Step 3 has landed — run it only after Step 3 is committed.
- Unit tests for `_scaffold_summary_for_pm`: with grounded decisions produces source summary without warning;
  with any `inferred` entry appends warning listing the inferred IDs; with no decisions produces no error;
  with both legacy `decisions` key and new `tech_decisions` present, both sections appear without
  double-counting or clobbering (the coexistence case Phase 1 deliberately maintains).
- Soft-gate non-blocking: integration or unit test that calls `_create_planning_ticket` with an
  `inferred`-source decision present; assert the ticket is created successfully (warning included, init not
  blocked). Verifies design §5's claim that the gate warns but does not block.
- `uv run pytest tests/ -q` — full suite green.

---

### 5. Operator confirmation (two render/threading functions + four `ask_sa_confirm` definitions)

**What:**
Thread `tech_decisions` and `size` from the stored proposal through the full confirm-prompt call chain — all
four `ask_sa_confirm` definitions (one Protocol abstract + three concrete) must be updated or `prompt_sa_confirm`
will raise `TypeError` on every call:

1. **`render_sa_confirm_prompt` (`init_workflow.py` ~line 1193)**: gains `tech_decisions: list[dict]` and
   `size: str`. Renders before the accept/reject prompt: project size + SA reasoning paragraph, then
   ID | choice | source_type | source_ref table. Size is displayed as read-only (override is out of scope).

2. **`prompt_sa_confirm` (`init_workflow.py` ~line 1227)**: after loading the proposal, extract
   `tech_decisions = proposal.get("tech_decisions", [])` and `size = proposal.get("size", "S")`; forward to
   `p.ask_sa_confirm(...)`.

3. **`PromptHandler` Protocol (`init_prompts.py:36`)**: add `tech_decisions: list[dict]` and `size: str` to
   the `ask_sa_confirm` abstract signature.

4. **`CliPromptHandler.ask_sa_confirm` (`init_prompts.py:108`)**: add `tech_decisions` and `size`; pass to
   `render_sa_confirm_prompt`.

5. **`AutoPromptHandler.ask_sa_confirm` (`init_prompts.py:224`)**: add `tech_decisions` and `size`; accept
   and ignore (eval/auto path skips the interactive prompt).

6. **TUI `ask_sa_confirm` (`tui/tui_prompts.py:121`)**: add `tech_decisions` and `size`; pass to
   `render_sa_confirm_prompt`.

**Why:** Operator sees grounded vs. inferred decisions and project size before accepting the scaffold proposal.
All four implementations must be updated atomically — any missing `ask_sa_confirm` signature breaks the
`PromptHandler` Protocol and causes a `TypeError` at the `prompt_sa_confirm` call site.

**Verify:**
- Unit test: call `AutoPromptHandler.ask_sa_confirm` (`init_prompts.py:224`) with a proposal carrying
  populated `tech_decisions` and `size`; assert no `TypeError` and that the auto-handler returns without
  attempting to render. This catches missed signature updates before the manual path.
- Seam test: mock `latest_scaffold_proposal` to return a payload with `tech_decisions` and `size`; call
  `prompt_sa_confirm`; assert it forwards both values to `p.ask_sa_confirm(...)` without `TypeError`. This
  verifies the Step 3→5 data path end-to-end — a renamed payload key in Step 3 would break this test while
  the `AutoPromptHandler` unit test above would still pass.
- Manual: `jig init --no-docker` against a test brief. Reach the SA confirmation prompt. Verify size
  selection and tech_decisions table render with source_type values visible. Verify empty table renders without
  error when `tech_decisions` is `[]`.

---

### 6. Dev/test role access

**What:**
- Grep first: `grep -n arch_get_field jig/defaults/roles/dev.yaml jig/defaults/roles/test.yaml` — confirm
  absent before editing
- Add `arch_get_field` to `allowed_tools` in both `dev.yaml` and `test.yaml`

**Why:** Dev and test agents get direct lookup access for mid-ticket architecture questions. PM already gets
`tech_decisions` via the planning-ticket description produced by `_scaffold_summary_for_pm` in Step 4.

**Verify:**
- Add tests to `tests/test_load_role_by_role_id.py` that load `dev.yaml` and `test.yaml` via `RoleConfig`
  and assert `"arch_get_field" in role.allowed_tools` for each. This is the same pattern used in Step 2
  and checks the property that actually matters, not a grep line count.
- `uv run pytest tests/test_load_role_by_role_id.py -q` — new tests pass.

---

### 7. Full verification

**What:** Run the complete gate locally.

**Verify:**
- `uv run ruff check jig/ tests/`
- `uv run ruff format --check jig/ tests/`
- `uv run pytest tests/ -q` — full suite green
- End-to-end: `jig init` (sandboxed, Docker on) against the hn-cli brief produces `architecture.yaml` with
  grounded `tech_decisions` (at least `cli-framework`, `http-client`, `async-io`; each with
  `source_type: context7` or `live_fetch`), the operator confirmation table renders correctly, and the
  planning ticket description includes a tech-decisions summary and an inferred-warning if applicable.

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
- 2026-06-07: Revised ×7 — add pre-Step-1 dev-agent pre-flight + Step 2 rollback note; rename item 4 to
  CliPromptHandler; add SA-accept seam test to Step 4 (proposal→apply_scaffold path); egress gate uses
  sandboxed jig init; Step 6 verify uses role loader; merge egress probe into Step 2; both-present test Step 4;
  prompt_sa_confirm seam test Step 5; design.md size override deferred
- 2026-06-07: Revised ×8 — keep strict_tools: true in Step 2 (add to allowed_tools, not weaken deny list);
  fix test assertion (strict_tools is True); mutable defaults: list[dict] | None = None + normalize in body
  for handle_sa_propose_scaffold and apply_scaffold
- 2026-06-07: Revised ×9 — TechDecision.id uses validate_kebab_id field_validator; add non-kebab rejection test
- 2026-06-08: Revised ×10 — Step 3 Literal["S","M"] (not L); Step 4 persists size to architecture.yaml;
  Step 7 drops hardcoded baseline count
- 2026-06-08: Revised ×11 — Step 5 heading/body: clarify "four" as Protocol + 3 concrete;
  Step 7: remove remaining baseline qualifier
- 2026-06-08: Revised ×12 — Step 4: add size: str = "S" param to apply_scaffold; write size
  unconditionally (not inside if tech_decisions); pass size from SA-accept call site; add
  size assertion to verify
- 2026-06-08: Revised ×13 — Step 2: sa.yaml documents only "S"/"M" for Phase 1 (not "L"); handler
  rejects "L" so advertising it causes guaranteed ValueError
- 2026-06-08: Revised ×14 — Step 2 phase_prompt description: "selects S/M/L" → "selects S/M in Phase 1"
