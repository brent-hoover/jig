---
title: Medium L0–L3 PO Pipeline — Design
type: design
status: active
owner: brent-hoover
created: 2026-06-09
updated: 2026-06-10
problem: ./problem.md
---

# Medium L0–L3 PO Pipeline — Design

## Summary

Make the L0–L3 PO pipeline the default init topology for the `medium` profile by **wiring** the existing
(built, tested) L0–L3 roles and MCP handlers into `jig init`'s resume state machine. The profile is
resolved **up front** via a guided size-selection prompt at the very top of `jig init` (small vs. medium,
with a decision framework + examples), so `classify_resume` can branch the PO topology before any PO runs:
`medium` → L0→L1→L2→L3→`sa_mvp`; non-medium → the existing v1 flat path. New `ResumeState` values for L0–L3
detect level completion the same way the v1 brief path does — via `Handoff` markers on each level's ticket
— and new `run_po_l0/l1/l2/l3_conversation` helpers mirror the existing `run_po_conversation`. The
level-resolution logic `/init --proceed` already implements is factored into a shared helper (rebased onto
thread markers) so the auto path and the manual command share one source of truth. `medium.yaml` flips
`sa_role: sa → sa_mvp`.

Because the profile is now chosen up front, the existing **PM-1 profile-selection pass**
(`PM_PROFILE_PASS` / `PM_PROFILE_CONFIRM_PROMPT`) no longer fires for interactive init — exactly as the
`--profile` flag already causes today. This is a deliberate, called-out consequence (see §2 + Risks).

## Level → ticket → handoff mapping (verified)

| Level | Role | Ticket id | `*_finalize` Handoff phase | Writes |
|-------|------|-----------|----------------------------|--------|
| L0 | `po-l0` | `project` (`L0_TICKET_ID`) | `po-l1` | `docs/brief.md`, `.jig/spec/project.structured.yaml` |
| L1 | `po-l1` | `discovery` (`L1_TICKET_ID`) | `po-l2` | `.jig/spec/discovery.md` (+ structured, playbacks) |
| L2 | `po-l2` | `suites` (`L2_TICKET_ID`) | `po-l3` | `.jig/spec/suites.yaml` |
| L3 | `po-l3` | `suite-<id>` (one per suite) | `sa` | `.jig/spec/suites/<id>/{brief.md,spec.structured.yaml}` |

(Refs: `po_l0_mcp.py:29,147`, `po_l1_mcp.py:71,1096`, `po_l2_mcp.py:55,285`, `po_l3_mcp.py:53–61,349`.)

Note L0 is the **`project`** ticket and `po-l0` role — **not** the v1 `brief` ticket / `po` role. v1 small
keeps `brief` + the separate `spec_generation` step; medium's L0 (`po-l0`) writes both `brief.md` and
`project.structured.yaml` in one step, so the medium branch never runs v1 `PO_CONVERSATION` /
`SPEC_GENERATION`. Within a single init only one topology runs, so the shared `project.structured.yaml`
path is never written by both.

## Approach

### 1. Up-front size selection (guided prompt)

Profile selection moves to the **top of `run_init`**, before the first PO spawn (the single insertion
point that covers both CLI `jig init` and TUI `/init`, since both call `run_init`):

**Resume-safe** — `run_init` is also the resume entrypoint, so the prompt/apply fires **only when no
profile is persisted yet** (`load_config(target).profile.name` empty after `create_stub`). A resumed init
(profile already chosen on the first run, no `--profile`) preserves it untouched — re-prompting would
overwrite the saved profile and could switch the PO topology mid-run.

- If `--profile <name>` was passed → use it (existing `run_init` early-apply, overrides), no prompt.
- Else if a profile is already persisted (resume) → keep it, no prompt.

**`--brief` is a flat-topology shortcut.** `--brief` bakes a single monolithic brief and pre-seeds the v1
`brief` ticket; the medium L0–L3 pipeline ignores that ticket (it reads `project`/`discovery`/`suites`),
so `--brief --profile medium` would silently drop into L0 interaction. It is therefore **rejected** with a
clear error; medium evals drive the full L0–L3 chain via the scenario harness (or a fixture that seeds the
L0–L3 artifacts), not a baked brief. (`--brief` still requires `--profile`, and is only valid with a
non-medium profile.)
- Otherwise (fresh init, no `--profile`), ask one guided size question via a new
  `PromptHandler.ask_project_size` (CLI / TUI / Auto
  impls, mirroring `ask_sa_confirm`). The prompt **teaches** the distinction:

  > **Small** — a simple, single-purpose project: one or two modules, no external integrations, no
  > compliance. *Example:* a CLI that reads one source and formats output (a Hacker News reader, a file
  > converter, a focused utility). → flat scaffold, generic SA, generalist reviewer.
  >
  > **Medium** — a multi-module project: distinct capability areas warranting separate modules, external
  > integrations (APIs / datastores), or compliance. *Example:* an applicant-tracking system (job-posting
  > + candidate + billing modules), or a web service with auth + database + third-party APIs. → per-module
  > architecture + boundary enforcement.

  (Framework text mirrors the `small.yaml` / `medium.yaml` profile descriptions so the operator's choice
  and the profile semantics stay consistent.)

The answer resolves to the `small`/`medium` profile and is applied via the existing `apply_profile` +
`save_config` path — **authoritatively**, exactly as `--profile` does — so `cfg.profile.sa_role` (and
`cfg.profile.name`) are set before any PO runs.

### 2. `classify_resume` branches on the resolved profile

Insert the branch **immediately after the `DirState` short-circuits** (`ALREADY_DONE` / `BROKEN`) and
**before** the `brief = await tickets.get("brief")` lookup, reading `cfg.profile.name`:

- **Not `medium`** → fall through to the existing v1 logic. `ALREADY_DONE` keys only off `project.yaml`'s
  `template_applied_at` (set at scaffold time, `init_workflow.py:83`), so partial L0–L3 state never trips
  it.
- **`medium`** → route through the new L0–L3 states (§3). The medium branch **never** returns
  `PO_CONVERSATION`, `NEEDS_ANSWER_BRIEF`, `BRIEF_APPROVAL`, `SPEC_GENERATION`, `GAP_PROMPT`,
  `PM_PROFILE_PASS`, or `PM_PROFILE_CONFIRM_PROMPT`.

**PM-1 profile pass retirement (behavioral change, called out).** The v1 PM-1 gate
(`init_workflow.py:~2024`) fires only when `cfg.profile.name` is empty. Since the up-front prompt always
sets a profile (as `--profile` already does), that gate becomes dead for interactive init — for **small
too**, not just medium. So this change **retires PM-1's interactive profile-selection pass**: small init
still runs its v1 `brief → approval → spec_generation → SA(sa)` mechanics unchanged, but the profile is
now chosen up front instead of by PM-1 mid-flow. This matches `--profile`'s existing behavior and removes
a redundant second profile decision. (If instead we must preserve PM-1 for small, see Open questions.)

### 3. New `ResumeState` values + thread-marker completion detection (medium branch)

```python
class ResumeState(str, Enum):
    ...
    PO_L0_CONVERSATION = "po_l0_conversation"   # po-l0 on `project`
    PO_L1_CONVERSATION = "po_l1_conversation"   # po-l1 on `discovery`
    PO_L2_CONVERSATION = "po_l2_conversation"   # po-l2 on `suites`
    PO_L3_CONVERSATION = "po_l3_conversation"   # po-l3 on `suite-<id>`
```

Detection uses **thread markers** (a `Handoff` on the level's own ticket), the same mechanism v1 uses on
the `brief` ticket — **not** `/init --proceed`'s artifact-on-disk checks. Each `*_finalize` posts a
`Handoff` (mapping table above) and resolves its ticket. The medium branch checks, in order:

1. No `Handoff` on `project` → `PO_L0_CONVERSATION`.
2. L0 handoff present, no `Handoff` on `discovery` → `PO_L1_CONVERSATION`.
3. L1 handoff present, no `Handoff` on `suites` → `PO_L2_CONVERSATION`.
4. L2 handoff present, some suite's `suite-<id>` ticket has no `Handoff(phase="sa")` (§4) →
   `PO_L3_CONVERSATION` for that suite.
5. Every suite handed off → `SA_CONVERSATION` (now `sa_mvp` per the profile's `sa_role`).

This is **new per-ticket scanning** (four tickets / four phases), structurally modeled on — but not a
literal reuse of — the v1 `brief`-ticket scan.

**`suite_id` source for the L3 arm.** `classify_resume` returns only a `ResumeState` (it must stay
signature-compatible with the existing dispatch); it does **not** carry the pending suite id. The
`PO_L3_CONVERSATION` dispatch arm **re-runs the shared `next_incomplete_level`** (§6) to obtain the pending
result and asserts the level is still L3 before spawning `po-l3`. The resolver returns a structured
`NextLevel(level, ticket_id, suite_id)` — for L3, `ticket_id` is the **`suite-<id>`** ticket while
`suite_id` is the **bare `<id>`** (`suite-catalog` vs. `catalog`); the L3 arm passes `result.suite_id` (not
the ticket id) to `run_po_l3_conversation`. One resolver is the single source of truth, and
`classify_resume` stays a pure `-> ResumeState` function.

### 4. L3 per-suite fan-out

L3 runs once per suite. `suites.yaml` (from L2) enumerates the suites; the shared level-resolution helper
(§6) returns the first suite whose `suite-<id>` ticket lacks a `Handoff(phase="sa")`, and the run loop
spawns `po-l3` for it. When every suite has handed off, the helper returns "L3 complete" and classify
advances to the SA. Per-suite progress is tracked by the per-suite tickets, so an interrupted run resumes
at the right suite.

### 5. Spawn helpers + the run loop

Add `run_po_l0_conversation`, `run_po_l1_conversation`, `run_po_l2_conversation`,
`run_po_l3_conversation(*, suite_id)` in `init_workflow.py`, mirroring `run_po_conversation`
(`init_workflow.py:~866`, which loads role `po` on ticket `brief`): each ensures its level ticket
(`project` / `discovery` / `suites` / `suite-<id>`), loads the matching `po-l*` role, spawns the agent, and
streams to the console. The `run_init` dispatch loop gains arms for the four new states; after each it
re-classifies (existing loop pattern), so **levels auto-cascade** — each level's handoff makes the next
`classify_resume` return the next level. No new per-level confirmation gate: the operator interacts within
each level's conversation; init-level gates remain the up-front size prompt and the end scaffold-confirm.

### 6. Shared level-resolution with `/init --proceed`

`_proceed` (`tui/commands/init.py`) currently resolves "which level is next" by **artifact-on-disk**
presence (`discovery_path(...).is_file()`, per-suite `brief.md`) and opens the right ticket. Factor this
into a shared `next_incomplete_level(project_path, tickets, threads) -> (level, ticket_id) | None`,
**rebased onto the thread-marker basis** of §3 (handoff on the level ticket), and call it from **both**
`classify_resume`'s medium branch and `_proceed`. This keeps the auto path and the manual command on one
detection convention and prevents L3-step drift. `_proceed` remains the manual stepping escape hatch.

### 7. Profile flip + missing-artifact behavior

- `medium.yaml`: `sa_role: sa → sa_mvp`; replace the deferred-note comment with "v2 init pipeline active."
- **Missing-artifact = fail loud in the spawn helper, not just the prompt.** `classify_resume`'s ordering
  guarantees `sa_mvp` is reached only after every L0–L3 handoff exists, so inputs are present by
  construction in the normal flow. For a hand-edited / partial project, the **SA spawn helper** (the
  arm that runs `sa_mvp`) checks its required upstream artifacts exist and raises a clear error before
  spawning — a hard failure surfaced to the operator, not a silent skip and not merely a prompt
  instruction in `sa_mvp.yaml`.

## Interfaces

- **`PromptHandler.ask_project_size(*, console) -> str`** — new abstract method on the `PromptHandler`
  Protocol (returns a profile name); CLI (guided prompt), TUI (round-trip), Auto (returns `"small"`
  default — see Risks) impls.
- **`ResumeState`** gains `PO_L0_CONVERSATION`, `PO_L1_CONVERSATION`, `PO_L2_CONVERSATION`,
  `PO_L3_CONVERSATION`.
- **`run_po_l0_conversation(...)`, `run_po_l1_conversation(...)`, `run_po_l2_conversation(...)`,
  `run_po_l3_conversation(*, suite_id, ...)`** — new spawn helpers in `init_workflow.py`.
- **`next_incomplete_level(project_path, tickets, threads) -> NextLevel | None`** — shared thread-marker
  level resolver used by `classify_resume` (medium branch) and `_proceed`. `NextLevel` carries `level`,
  `ticket_id`, and `suite_id` (the bare suite id for L3 — `ticket_id` minus the `suite-` prefix — else
  `None`). `None` return = all levels complete → SA.
- **`medium.yaml`** `sa_role` becomes `sa_mvp`.
- No changes to the L0–L3 role files or the `*_mcp.py` handlers; `sa_mvp.yaml` unchanged (the fail-loud
  check lives in the spawn helper).

## Data model

No new persisted schema. Tickets (`project`, `discovery`, `suites`, `suite-<id>`, `architecture`) and
artifacts are already produced by the existing handlers — see the mapping table above. The shared
`docs/brief.md` + `.jig/spec/project.structured.yaml` are written by `po-l0` on the medium branch
(replacing v1 `po` + `spec_generation`) and by the v1 path on the small branch; only one runs per init.

## Alternatives considered

### Simplest — artifact-on-disk detection, `--profile`-only

Add L0–L3 states detected by file presence (reuse `_proceed` verbatim); require `--profile medium`;
auto-cascade; flip `sa_role`. *Drawbacks:* two completion-detection styles in one classifier (disk vs. the
v1 thread-marker convention); medium not default-reachable without the flag; no guided self-classification.

### Complete — guided up-front size prompt, thread-marker detection, shared resolver (chosen)

As above. Reuses the v1 detection *mechanism*, keeps small's PO+SA mechanics unchanged, reuses the existing
roles/handlers/tickets and `_proceed`'s gating via a shared resolver, makes medium default-reachable with a
teaching prompt. *Drawbacks:* more wiring (4 states + 4 helpers + a prompt + the shared-resolver refactor);
retires PM-1's interactive profile pass; the operator self-classifies size up front (mitigated by the
framework).

### Optimal — declarative init-pipeline engine

Each profile's level sequence as data (a pipeline config), mid-level checkpoint/resume, operator-visible
L0→L3 progress UI. *Trades away:* a sizable abstraction for two topologies; revisit if a third topology or
per-level checkpointing appears.

### Decision

**Complete**, with up-front guided size selection (problem open-question option B). The resume/failure
drivers rule out Simplest's split detection convention; the Optimal engine is unjustified for two
topologies. The "decide-after-L0 / unify the brief step" idea (option C) is deferred — it reworks small's
brief + spec-generation, fenced off by the problem.

## Risks

- **Resume correctness across four new tickets.** The medium init spans L0→L1→L2→(L3×suites)→SA, and
  detection is **new per-ticket handoff scanning** (`project`/`discovery`/`suites`/`suite-<id>` for phases
  `po-l1`/`po-l2`/`po-l3`/`sa`) — modeled on, not literally reused from, v1. Mitigation: a resume test
  that stops after each level and asserts the next `classify_resume` returns the correct state, asserting
  the exact (ticket id, handoff phase) at each boundary; drive L3 fan-out off the per-suite tickets.
- **PM-1 retirement touches the small path.** Small init no longer runs the PM-1 profile pass (profile set
  up front). Its `brief→approval→spec→SA` mechanics are unchanged, but any small test/scenario that
  exercised PM-1 profile selection must be updated. Mitigation: explicit in §2; update the small init
  test to assert the up-front-profile path; flagged for sign-off (Open questions).
- **Operator misclassifies size up front.** Guided framework reduces this; a wrong "small" yields a flat
  scaffold for a multi-module project. Mitigation: clear examples; re-init corrects it.
- **Auto-cascade hides where the operator is.** Four PO levels run back-to-back; mitigated by per-level
  console streaming + the existing per-spawn status UI.
- **`sa_mvp` runs from a live `jig init` for the first time** (only bones-tested today). Mitigation: the
  e2e scenario (plan) drives the full medium auto-init and asserts module artifacts land.

## Out of scope

- Building/changing the L0–L3 roles, `*_mcp` handlers, or `sa_mvp` (the fail-loud check is in the SA spawn
  helper, not the role).
- The `small` PO+SA mechanics and the v1 flat path (beyond PM-1 retirement).
- Unifying the brief step on `po-l0` for all sizes (option C) — separate future effort.
- A declarative pipeline engine (Optimal).
- Module-boundaries enforcement (already shipped; activates as a consequence).

## Open questions

- [x] **PM-1 retirement sign-off** — RESOLVED (approved 2026-06-09): retire the interactive PM-1
  profile-selection pass. The up-front size prompt sets the profile authoritatively for both sizes
  (mirroring `--profile`); small's `brief→approval→spec→SA` mechanics are otherwise unchanged. Small
  test/scenarios that exercised PM-1 profile selection must be updated (see plan).
- [x] **`AutoPromptHandler` default + eval guard** — RESOLVED (in plan step 1): `ask_project_size` returns
  `"small"` defensively in the auto handler; the plan adds a guard/assert that eval entrypoints pass
  `--profile`, so a future eval that forgets it doesn't silently get `small` on a medium project.

## Change log

- 2026-06-09: Initial draft (brent-hoover)
- 2026-06-09: Revised ×1 (design-reviewer) — fix L0 ticket (`project`, not `brief`) + add
  `run_po_l0_conversation`; add the verified level→ticket→handoff mapping table; state the medium branch
  skips all v1 states; call out PM-1 interactive-profile-pass retirement as a deliberate small-path change;
  rebase the shared level resolver onto thread markers (not disk); locate the fail-loud missing-artifact
  check in the SA spawn helper; pin the up-front-prompt insertion point (`run_init`) and the classify_resume
  branch position (after DirState, before the brief lookup).
