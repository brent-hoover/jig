---
title: Code-Quality System — Design
type: design
status: active
owner: Brent Hoover
created: 2026-05-28
updated: 2026-05-28
problem: ./problem.md
---

# Code-Quality System — Design

## Summary

A unified code-quality layer built on jig's already-merged pieces. A single jig-owned `taxonomy.yaml` manifest
routes each AI-defect pattern to a detection engine (ruff, Semgrep, or judgment-only) and an owning reviewer.
The deterministic engines run as a jig-owned curated pass over agent output, producing `TaxonomyHit`s that (a)
extend the existing `ChangeMetrics` signal into reviewer prompts and the commit log, and (b) feed the
canonicalizer's now-functional Semgrep path. A new "surface-to-reviewer" disposition routes non-autofixable
findings to the federation. All quality dimensions are recorded over time in the existing `AuditStore`, and
`jig audit report` gains a delta view so a quality change can be attributed to a prompt/role/workflow change.

The work spans GitHub sub-issues #109–#112 (A–D). The sections below are grouped by concern, so they cross
and overlap those issues rather than mapping one-to-one (the per-section "sub-issue" tags show which issues
each touches). Each sub-issue gets its own `plan.md` when picked up.

## Approach

### 1. `taxonomy.yaml` — the routing manifest (single source of truth) — sub-issue B

A jig-shipped file (`jig/defaults/code_quality/taxonomy.yaml`), not per-project. One entry per taxonomy
pattern:

```yaml
- id: TAX-ERR-001
  category: error-handling
  title: swallowed exception
  mechanism: "models emit except/pass to make code 'run' under pedagogical bias"
  detection:
    kind: ruff            # ruff | semgrep | judgment
    ref: "S110"           # ruff code, semgrep rule id, or null for judgment
  owning_reviewer: reviewer-error-handling   # must be a known LLM reviewer id (see §4)
  cue: "except Exception: pass / bare except with no re-raise"
  source: "https://kenrinzero.github.io/ai-code-audit-taxonomy/#swallowed-exception"
```

Every field is consumed: the engines read `detection` to learn which ruff code / semgrep rule implements a
pattern (§2); reviewer enrichment reads `cue` + `mechanism` + `owning_reviewer` (§4); measurement counts hits
per `id`/`category` (§5); `source` is the upstream provenance link for curation. (The upstream taxonomy's
`evidence_grade` / `difficulty` annotations are deliberately **not** carried into the runtime schema — they're
useful for *deciding* which patterns to wire and in what order, which is a plan-time concern, not a field any
code reads. Keeping them out avoids dead fields.) The manifest is the durable contract — treated like a spec,
changed deliberately.

### 2. Deterministic engine — jig-owned curated pass — sub-issues A + B

Two sources, both jig-owned for comparability (independent of the target project's own config):

- **ruff layer**: a curated select list (existing defaults + `S`/`B`/`ASYNC`/`LOG`) run as a dedicated pass on
  the changed files. Findings map by code → taxonomy `id` via the manifest.
- **Semgrep layer**: a shipped rule-pack at `jig/defaults/rules/semgrep/ai-taxonomy/*.yml`, each rule `id`
  matching a manifest `detection.ref`. `semgrep` becomes a real dependency and is made available in the
  Docker/bwrap sandbox; it runs fully offline (`--config <local>`, telemetry/metrics disabled, no registry
  fetch). This closes the inert-engine gap (#17 plumbed discovery via `list_semgrep_rule_paths` but shipped no
  rules and no binary).

Both run over the changed-file set and emit `list[TaxonomyHit]`.

### 3. Where it runs — reuse the `code_metrics` rail — sub-issue A/B

`jig/code_metrics.py` already computes `ChangeMetrics` over changed files and feeds reviewer prompts + the
commit log. Extend it (or a sibling `compute_quality_signal`) to also run the ruff + Semgrep taxonomy passes
and attach `taxonomy_hits`. The post-merge **canonicalizer agent** invokes the same Semgrep rule-pack — shared
rules, two stages, different dispositions for what a hit triggers:

- **Pre-merge, per-worktree** (the `code_metrics` rail): **surface-to-reviewer only** — hits become signal in
  the federation prompt (§4). No code mutation; this is the review-time signal.
- **Post-merge canonicalizer**: the existing #17 behavior — **autofix** where the matched rule carries a
  `fix:`, **escalate-to-issue** (`CanonicalizationIssueStore`) where it doesn't. (It may also record hits to
  the snapshot in §5.) No reviewer is in the loop at this stage.

The same rule-pack thus serves review-time signal and post-merge enforcement without duplication.

### 4. Reviewer-facing disposition — sub-issue C

Today a canonicalization finding has two fates: autofix or escalate-to-issue (`CanonicalizationIssueStore`).
Add a third: **surface-to-reviewer**. This stage injects two *distinct* things into each owning reviewer's
prompt (reusing the "Objective Code Metrics / findings" section added in PR #107):

1. **Deterministic hits** — `TaxonomyHit`s that ruff/Semgrep actually fired on in this diff, with `file:line`.
   These are concrete "here is an instance" findings.
2. **Judgment checklist** — for `kind: judgment` patterns there is **no engine and no hit**; instead the
   manifest's `cue` + `mechanism` for that category are injected as a static "look for these" checklist, and
   the LLM reviewer detects them by judgment. This closes the gap that judgment patterns can never produce a
   `TaxonomyHit` (whose `source` is only `ruff` | `semgrep`).

So a reviewer's block = (its category's deterministic hits, if any) + (its category's judgment checklist,
always). Mechanical reviewers and the autofix path are untouched.

**`owning_reviewer` → federation mapping.** `owning_reviewer` must be a value in
`known_llm_reviewer_ids()` (the spawnable LLM reviewers in `jig/reviewers/dispatch.py`). The taxonomy's surface
categories (14, spanning 24 patterns, **as of the taxonomy version linked in `problem.md`**) map onto the
*existing* reviewers — no new reviewer roles are created by this initiative. The illustrative mapping below
sets the policy; the authoritative per-category assignment is finalized when `taxonomy.yaml` is authored in
sub-issue B (and must be re-checked if the upstream taxonomy changes its category set):

- Error Handling → `reviewer-error-handling`; Security → `reviewer-security`; Testing → `reviewer-test-adequacy`.
- All other categories (Reliability, Async, Observability, Control Flow, Structure, Language Pitfall,
  Configuration, Consistency, Documentation, Defensive Programming, Library Usage) → `reviewer-pattern-conformance`
  as the catch-all, with `reviewer-generalist` as the fallback when pattern-conformance isn't selected for the
  ticket.

A manifest entry whose `owning_reviewer` is not in `known_llm_reviewer_ids()` is a validation error (fail
loud at load), mirroring how `dispatch_with_llm_spawn` already rejects unknown reviewer ids.

**When the owning reviewer isn't selected for a ticket** (a reviewer is in `known_llm_reviewer_ids()` but
neither it nor its fallback is in the ticket's selected set), the hits/checklist are **not** silently dropped:
they are logged at warning level ("N taxonomy findings for category X had no selected reviewer this run"), and
— critically — the deterministic hits are still recorded to the §5 `quality_snapshot` regardless of reviewer
selection. Measurement does not depend on whether a reviewer happened to be in the set; only the *surfacing*
does. This mirrors the problem doc's "degrade with a logged warning, never silent" contract.

### 5. Measurement / attribution — the north star — sub-issue D

Record a per-run `quality_snapshot` to `AuditStore`: max CC, ruff finding count, taxonomy hit counts by
category, canonicalization fixes applied, escalations raised — tagged with the run plus the active prompt /
role / workflow identifiers (a "cell", mirroring the eval harness's `Cell`). `jig audit report` gains a
quality view: dimensions over time and a `--compare` delta across two cells, so "did this prompt change
improve or regress quality?" is answerable.

## Interfaces

- **`taxonomy.yaml`** schema (above) — the durable, human-edited contract.
- **`TaxonomyHit`** (pydantic, frozen): `id`, `category`, `file`, `line`, `source: "ruff" | "semgrep"`,
  `reviewer`. A concrete deterministic detection, returned as part of the `code_metrics` computation (whether
  by extending `ChangeMetrics` or via a wrapper is the open question below — left undecided here). Judgment
  patterns produce **no** `TaxonomyHit` — they are rendered as a static checklist from the manifest (§4), so
  there is intentionally no `"judgment"` source value.
- **`AuditStore`** gains a `quality_snapshot` record kind (new `RuleSource` value or a sibling record).
- **CLI**: `jig audit report --quality [--since <ref>] [--compare <cellA> <cellB>]`.
- **Semgrep**: rule-pack directory contract (`jig/defaults/rules/semgrep/ai-taxonomy/`) layered with the
  per-project `.jig/rules/semgrep/` that `list_semgrep_rule_paths` already discovers.

## Data model

- `TaxonomyHit` — transient per-run, surfaced to prompts and rolled into the snapshot.
- `quality_snapshot` — append-only `AuditStore` entry; keyed by `run_id` + ticket + cell tags. No migration
  needed (no production data; regenerable).

## Alternatives considered

### Pure-Python AST detection instead of Semgrep

Hand-write `ast`-based checks for the cue patterns. Rejected: #17 already chose and plumbed Semgrep
(`to_semgrep_rules`, `list_semgrep_rule_paths`); Semgrep expresses cues declaratively with far less bespoke
code. Reviving the existing path beats abandoning it for hand-rolled AST.

### Single composite quality score

One number for "quality." Rejected: a composite hides *what* moved (complexity vs. security vs. test gaps).
The eval harness already reports static metrics as a vector; we follow that — a vector of dimensions, not a
collapsed score.

### Per-project ruleset for the signal

Use each target project's own ruff/semgrep config. Rejected during brainstorming: the signal wouldn't be
comparable across runs/projects, which defeats the measurement goal. The deterministic pass is jig-owned.

### Chosen: manifest-routed engines on the `code_metrics` rail + AuditStore measurement

One manifest drives ruff + Semgrep + reviewer enrichment + measurement; everything reuses merged components
(`code_metrics`, canonicalizer, `AuditStore`, reviewer federation). Smallest net-new surface for the scope.

## Risks

- **Semgrep in the sandbox** — binary availability inside Docker/bwrap, run-time cost, and ensuring it never
  reaches the network (registry/telemetry off). This is the primary integration risk and is sub-issue A's
  whole job to de-risk first. The exact install mechanism (a `pyproject.toml` dependency for the host plus a
  `Dockerfile` provision so the binary exists in-sandbox) is settled in sub-issue A's `plan.md`, not here.
- **Attribution needs stable cell identifiers** — per-run attribution assumes we can capture the active
  prompt/role/workflow versions. If those identifiers don't exist yet, sub-issue D must add them, or
  attribution degrades to coarse per-run.
- **`taxonomy.yaml` drift** — it's a curated fork of an upstream narrative taxonomy; it can go stale. Mitigate
  by keeping entries minimal and citing the source per entry.
- **Reviewer-prompt bloat** — many hits could swell prompts. Mitigate by scoping each reviewer to its own
  category and capping/aggregating.

## Out of scope

- Autofixing low-confidence AI-defect patterns (signal-only).
- Non-Python languages.
- A real-time dashboard (CLI report only).
- Semantic refactoring / rule-conflict resolution (stays human, per #17's non-goals).

## Open questions

Resolved from problem.md:

- **Composite vs. vector score** → vector of dimensions (no collapsed score).
- **Attribution granularity** → per-run, tagged with a (prompt, role, workflow) cell.

Still open (resolve in the relevant sub-issue's plan):

- [ ] Exact `quality_snapshot` schema and whether it's a new `AuditStore` record or an extended `AuditEntry`.
- [ ] Extend `ChangeMetrics` with `taxonomy_hits`, or introduce a `QualitySignal` wrapper around it?
- [ ] Whether the eval-coverage scoreboard (#112 stretch) ships in this initiative or a follow-on.

## Change log

- 2026-05-28: Initial draft (Brent Hoover)
- 2026-05-28: Review revisions — distinguished judgment checklist from deterministic `TaxonomyHit`s; specified
  `owning_reviewer` → `known_llm_reviewer_ids()` mapping; dropped unused `evidence_grade`/`difficulty` from the
  schema (added `source`); softened the section↔sub-issue mapping; named pre/post-merge dispositions; flagged
  the Semgrep install mechanism as sub-issue A plan-scope (Brent Hoover)
- 2026-05-28: Review round 2 — made the Interfaces `TaxonomyHit` note agnostic on the ChangeMetrics-vs-wrapper
  open question; specified the no-selected-reviewer path (logged warning + still recorded to snapshot, never
  silent drop); anchored the "14 categories" count to the linked taxonomy version and deferred the
  authoritative mapping to sub-issue B (Brent Hoover)
- 2026-05-28: Approved; status draft → active (Brent Hoover)
