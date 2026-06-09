---
title: Module Boundaries — Design
type: design
status: draft
owner: brent-hoover
created: 2026-06-08
updated: 2026-06-08
problem: ./problem.md
---

# Module Boundaries — Design

## Summary

Add a validated, per-module boundary declaration the SA writes at init time, compile it into semgrep
deny rules, and enforce those rules at the existing dev commit gate. A `BoundariesFile` schema captures
each module's allowed/forbidden internal (cross-module) and external (package) dependencies plus an
ontology glossary; a `sa_write_boundaries` MCP tool (parallel to the existing module-contract upserts)
writes `.jig/spec/modules/<m>/boundaries.yaml`; `generate_boundary_rules` runs at the end of
`arch_finalize` and compiles every module's boundaries into `.jig/rules/semgrep/boundaries/<m>.yml`;
and a new step in `commit_worktree` runs semgrep against the dev worktree, failing the gate loudly on a
violation and warning (never silent-green) when semgrep is unavailable. Module ids map to packages by a
fixed convention — kebab→snake under the project package — so no new schema field is required for the
mapping.

## Approach

Four components, in dependency order.

### 1. `BoundariesFile` schema (`jig/schemas/arch.py`)

A new validated model alongside the existing SA output schemas, reusing `ChangeLogEntry`
(`jig/schemas/arch.py:113`):

```python
class OntologyTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: str
    definition: str

class InternalBoundaries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_modules: list[str] = Field(default_factory=list)
    forbidden_modules: list[str] = Field(default_factory=list)
    rationale: str | None = None

class ExternalBoundaries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    rationale: str | None = None

class BoundariesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_version: int = 1
    module: str                                   # kebab-case module id (validate_kebab_id)
    ontology: list[OntologyTerm] = Field(default_factory=list)
    internal: InternalBoundaries = Field(default_factory=InternalBoundaries)
    external: ExternalBoundaries = Field(default_factory=ExternalBoundaries)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)
```

`module` is kebab-validated (matching the other id-bearing arch models). `internal`/`external` carry
both allow- and forbid-lists; **semantics** (below) compile these into enforceable deny rules.

### 2. `sa_write_boundaries` MCP tool (`jig/sa_incremental_mcp.py` + `jig/mcp_server.py`)

A handler parallel to the existing per-module upserts (`handle_module_set_external_dependency`, etc.):
`handle_sa_write_boundaries(*, project_path, boundaries: dict)` takes a **full `BoundariesFile` payload**
(including `spec_version`, `module`, `ontology`, `internal`, `external`, `change_log`) — mirroring how the
`module_set_*` upserts accept a complete model dict — validates it against `BoundariesFile`, and writes
`.jig/spec/modules/<m>/boundaries.yaml` (atomic write, `sort_keys=False`, canonical `model_dump`). The SA
supplies `change_log` entries on each (re)write (`revision >= 1` + `date`), as it does for the other arch
artifacts; the handler does not auto-increment. It does **not** generate rules — generation is deferred to
`arch_finalize` so a half-written boundary set never produces stale rules.

Wiring (three places, like every other module tool):
- `handle_sa_write_boundaries` in `jig/sa_incremental_mcp.py`;
- the `sa_write_boundaries` `@tool` registration in `jig/mcp_server.py`;
- the role surface in `jig/defaults/roles/sa_mvp.yaml` — both its machine `allowed_tools` list **and**
  the prose tool catalog the SA reads (the `module_set_*` documentation block), since that role documents
  each tool in prose, not just by name.

### 3. `generate_boundary_rules(project_path)` (new, e.g. `jig/boundary_rules.py`)

Runs at the end of `handle_arch_finalize` (`sa_incremental_mcp.py:1414`). Steps:

1. Resolve the project's top-level package: `top_pkg = config.project.name` with `-`/space → `_`,
   lowercased — the same derivation the scaffolder uses (`init_workflow.py:1535`). Code lives under the
   shipped `src/<top_pkg>/` layout.
2. Resolve **two distinct sets**: (a) the full module-id set — the modules declared in
   `architecture.yaml` (set via `arch_set_module`, which does **not** create a per-module dir), unioned
   with any subdir of `.jig/spec/modules/` (to catch a boundaries-only module not yet in
   architecture.yaml) — used for allow-list compilation and reference validation; and (b) the modules that
   actually declare boundaries — a direct glob of `.jig/spec/modules/*/boundaries.yaml`. Do **not** use
   `_collect_authored_module_ids` for either: it keys on `contracts.yaml` (`sa_incremental_mcp.py:1390`),
   so a module with a `boundaries.yaml` but no `contracts.yaml` would be invisible to it. Load + validate
   each boundaries file from set (b).
3. **Validate references**: every id in a module's `internal.allowed_modules` / `forbidden_modules` must
   be a known module id (set (a)); an unknown id (typo) is a generation-time **hard error**, not a
   silently-dropped target — a typo'd `allowed_modules` entry would otherwise over-deny. Likewise a
   boundaries file whose `module` field doesn't match its directory is a hard error.
4. **Missing package dir is fine.** The rule scopes to `src/<top_pkg>/<M_snake>/**`, but that dir need
   not exist at generation time — generation runs at `arch_finalize` (init), before dev agents scaffold
   per-module code. A rule scoped to a not-yet-existing dir matches nothing until the code lands (no code,
   nothing to enforce); it is **not** treated as an error. The only true silent-green is code authored at
   a path that doesn't match the convention — a documented layout limitation (Risks).
5. For each module M, emit `.jig/rules/semgrep/boundaries/<M>.yml` (one file per module) containing the
   compiled deny rules (below).
6. **Idempotent + atomic on failure**: validate and compile *all* modules before mutating the output dir,
   then clear + fully regenerate `boundaries/`. Rule ids are deterministic functions of (module, target),
   so identical inputs yield byte-identical files; a validation failure never leaves the project with
   deleted/partial rules.

**Allow-list soundness.** `arch_finalize` can be re-run. Allow-list compilation ("deny every module not
in `allowed_modules`") is only correct when *every* module that will exist is present at the finalize that
generates the rules — a module added in a later finalize re-widens the others' deny sets on the next run.
Full regeneration (step 6) keeps this consistent on every finalize; the contract is that the rules reflect
the module set *as of the most recent finalize*, which is the authoritative one.

**Deny-rule compilation.** semgrep matches patterns, not their absence, so enforcement is deny-list
based:

- **Internal** — for module M (package dir `src/<top_pkg>/<M_snake>/`), the forbidden target set is:
  - `internal.forbidden_modules` directly; **plus**
  - if `internal.allowed_modules` is non-empty (allow-list semantics): every *other* module id minus
    `allowed_modules` minus M itself. The generator has the full module set, so "deny everything not
    allowed" is computed into concrete deny targets.
  - Each forbidden module N → a rule banning `import <top_pkg>.<N_snake>` and
    `from <top_pkg>.<N_snake>[...] import …`, scoped via `paths.include: [src/<top_pkg>/<M_snake>/]`.
- **External** — `external.forbidden` packages → a rule banning `import <P>` /
  `from <P>[...] import …`, scoped to M's package dir. `external.allowed` is **advisory only** (you
  cannot deny "every pip package not in a list"); it documents intent and is not compiled.

### 4. Dev-gate enforcement (`jig/worktree.py`)

`commit_worktree` already runs `_auto_lint` (ruff) and raises `LintError` on unfixable issues — this is
the orchestrator-side gate, running **outside** the agent sandbox. Its current signature is
`commit_worktree(worktree_path, message)` (`worktree.py:465`), and the worktree lives *under* the project
root at `project_path/.jig/worktrees/<id>` — so the generated rules at `project_path/.jig/rules/semgrep/`
are **not inside the worktree** and are **not committed to the dev branch**. The check therefore needs the
project root explicitly.

**No signature change**: `project_path` is derivable, not threaded. Worktrees always live at
`project_path/.jig/worktrees/<id>` (`worktree.py:94`), so `_boundary_check` computes
`project_path = worktree_path.parents[2]` and **asserts** the `.jig/worktrees/` layout (fail loud if the
shape is unexpected, never silently skip). This avoids churning `commit_worktree`,
`handle_commit_progress` (`ticket_mcp.py:542`, which has no `project_path`), and
`_auto_commit_worktree`. Add `_boundary_check(worktree_path)` after `_auto_lint`:

1. If `project_path/.jig/rules/semgrep/boundaries/` has no `*.yml` files → no-op (no boundaries declared).
2. If the `semgrep` binary is unavailable (not on `PATH`) → emit a **visible** warning (run record +
   agent-facing note), return without failing. Loud-degradation: enforcement skipped, never a clean pass.
3. Otherwise invoke (fixed arg list, no shell):
   ```
   semgrep --metrics off --json --quiet \
           --config <project_path>/.jig/rules/semgrep/boundaries/ <worktree_path>
   ```
   `--metrics off` keeps it offline (jig's established semgrep convention; default semgrep phones home).
   The rule dir is passed as an absolute project-root path; the scan target is the worktree path.
   **Exit-code contract** (semgrep): `0` = no findings → pass; `1` = findings → parse the JSON `results`
   and raise `BoundaryViolationError` (sibling of `LintError`) with one message per finding
   (`module '<M>' may not import '<target>'`, derived from the rule id + message); `>= 2` = semgrep
   itself errored (bad rule, crash) → this is the **loud-degradation** path (visible warning), **not** a
   `BoundaryViolationError`, so a tool failure never masquerades as a boundary violation and never as a
   clean pass. A single whole-dir invocation is used; per-module attribution comes from the rule id
   (`boundary-<M>-...`), which is sufficient for the message contract.

The existing gate-failure path surfaces `BoundaryViolationError` to the dev agent, which fixes and
re-commits — same loop as a lint failure. Boundary rules apply only to the module package dirs they scope,
so a worktree touching unrelated code is unaffected.

## Interfaces

- **MCP tool** `sa_write_boundaries(boundaries: dict)` → accepts a full `BoundariesFile` payload
  (`spec_version`, `module`, `ontology`, `internal`, `external`, `change_log`), validates it, writes
  `.jig/spec/modules/<m>/boundaries.yaml`, returns an ack string. Wired in `sa_incremental_mcp.py`,
  `mcp_server.py`, and `sa_mvp.yaml` (machine list + prose catalog).
- **`generate_boundary_rules(project_path: Path) -> list[Path]`** — returns the rule files written;
  invoked at the tail of `handle_arch_finalize`. Raises on an unknown module reference or a missing module
  package dir (it must not emit unscoped/green-passing rules).
- **Rule files**: `.jig/rules/semgrep/boundaries/<module>.yml` — semgrep rule manifests (`.yml`, under the
  existing `.jig/rules/semgrep/` root for consistency; the dev gate invokes the `boundaries/` dir directly,
  not via the non-recursive `list_semgrep_rule_paths` glob, so co-locating here is safe).
- **Boundary file**: `.jig/spec/modules/<module>/boundaries.yaml` — conforms to `BoundariesFile`.
- **`commit_worktree(worktree_path, message)`** — signature **unchanged**; gains an internal
  `_boundary_check(worktree_path)` step that derives `project_path` from the worktree layout. New
  `BoundaryViolationError(Exception)` with a `violations: list[str]` payload, surfaced like `LintError`.

## Data model

```yaml
# .jig/spec/modules/job-posting/boundaries.yaml
spec_version: 1
module: job-posting
ontology:
  - term: Candidate
    definition: A person who has submitted an application for this posting.
internal:
  allowed_modules: [candidate, shared-types]
  forbidden_modules: [billing, auth]
  rationale: Job posting reads candidate refs but must not reach into billing/auth.
external:
  allowed: [httpx, pydantic]
  forbidden: [requests]
  rationale: Standardized on httpx for injectable transport.
```

```yaml
# .jig/rules/semgrep/boundaries/job-posting.yml  (generated; project pkg = "ats")
rules:
  - id: boundary-job-posting-no-internal-billing
    languages: [python]
    severity: ERROR
    message: "module 'job-posting' may not import 'billing' (forbidden cross-module dependency)"
    paths:
      include: ["src/ats/job_posting/"]
    patterns:
      - pattern-either:
          - pattern: import ats.billing
          - pattern: from ats.billing import $X
  - id: boundary-job-posting-no-external-requests
    languages: [python]
    severity: ERROR
    message: "module 'job-posting' may not import 'requests' (forbidden external dependency)"
    paths:
      include: ["src/ats/job_posting/"]
    patterns:
      - pattern-either:
          - pattern: import requests
          - pattern: from requests import $X
```

The example shows the **intent**; the exact pattern set that reliably matches every import form —
plain `import pkg`, `from pkg import x`, and crucially nested submodule imports like
`import pkg.sub` / `from pkg.sub import x` (semgrep metavariable binding across dotted paths is not
guaranteed) — is **validated against the installed semgrep in the plan's first step** before the generator
pins them. If metavariables don't bind across dotted imports, the generator falls back to a
`pattern-regex` on the import path. The coverage claim is a plan precondition, not an assumption.

## Alternatives considered

### Simplest

External-only, no new schema: SA records banned packages in free text; a minimal generator emits
external-import deny rules; the dev gate runs them. *Drawback (load-bearing):* it does not enforce internal
cross-module coupling — the primary isolation goal in the problem statement — so it misses the failure mode
the feature exists to catch.

### Complete

Validated `BoundariesFile` (internal + external + ontology) + `sa_write_boundaries` tool +
`generate_boundary_rules` (idempotent) + a loud-degrading semgrep step in `commit_worktree`. Covers both
internal and external isolation, validated and deterministic, reusing the existing gate and semgrep
toolchain. No new runtime machinery; module→package via the decided convention.

### Optimal

Complete, plus bidirectional consistency checks (a `forbidden` module can't also appear as a declared
`consumes` contract), ontology-term linkage into reviews, auto-suggested fixes on violation, and a
federation "boundary reviewer" complementing the static gate. *Trades away:* significant extra mechanism
and time for value that mostly matters once many modules exist.

### Decision

**Complete.** The isolation goal (problem statement) rules out Simplest — internal cross-module
enforcement is the whole point. The Optimal additions (contract↔boundary consistency, a boundary
reviewer) are deferred: there is no active profile producing multi-module projects yet, so the marginal
value is low until Phase 2a lands. Complete delivers validated, enforced internal + external boundaries
on the current module-producing SA path with no new runtime surface.

## Risks

- **Convention assumes the shipped `src/<pkg>/` layout.** Rules scope to `src/<top_pkg>/<M_snake>/**`.
  Generation runs at `arch_finalize` (init), *before* dev agents scaffold per-module code, so a
  not-yet-existing module dir is normal and must NOT block generation — the rule simply matches nothing
  until the code lands (no code, nothing to enforce; not silent-green). The true silent-green is code
  authored at a path that doesn't match the convention (flat / non-`src` layout, or a module dir named
  differently than `<M_snake>`): the rule then never matches the real code. v1 targets the shipped
  `src/`-layout templates; non-standard layouts are a documented limitation.
- **semgrep absence = no enforcement.** Handled by the loud-degradation path (visible warning, never
  silent pass), but a project that *expects* enforcement and runs without semgrep gets none. Acceptable
  for v1; a future hard-fail mode could be config-gated.
- **Allow-list compilation depends on the full module set and correct ids.** It runs at `arch_finalize`
  after all modules are set, and a re-finalize fully regenerates — but a typo in `allowed_modules`
  (referencing a non-existent module) would over-deny. Mitigation: generation **hard-errors** on any
  `allowed_modules`/`forbidden_modules` id not in the authored module set, so typos surface immediately
  rather than silently widening denial.
- **Indirect imports evade static rules.** The rules catch direct absolute imports, the dotted
  parent-import form, and package-**relative** sibling imports (`from ..billing import x`,
  `from .. import billing`) from a module's **root** files (a separate rule scoped to `<pkg_dir>*.py`,
  depth 0) — verified in `tests/test_boundary_rules.py`. Relative rules are deliberately *not* applied to
  nested files: there `..` resolves *inside* the module (e.g. `ats.job_posting.billing`), so a subtree-wide
  relative rule would false-positive on intra-module subpackages. Residual gaps: a relative sibling import
  from a nested (non-root) file, or reaching another module via a re-export / `importlib`. Accepted — static
  analysis is a strong default, not a sandbox.
- **String-pattern false negatives/positives** on unusual import forms (aliased package roots, relative
  imports across package boundaries). The `paths.include` scoping plus the four pattern variants cover the
  common forms; exotic cases are a known gap.

## Out of scope

- Runtime / import-hook enforcement — static only.
- Non-Python languages.
- SA unification (Phase 2a) and the medium PO-topology decision.
- Enforcing `external.allowed` as a positive allow-list (not expressible as semgrep deny rules).
- Machine-checking code/prose against `ontology` terms — captured for humans/agents only.
- Contract↔boundary consistency checks and a federation boundary reviewer (Optimal; deferred).

## Open questions

- [ ] **[Plan step 1, blocking]** Confirm the semgrep pattern set that matches all Python import forms —
  plain, `from … import`, and nested submodule (`import pkg.sub`) — against the installed semgrep version,
  and fall back to `pattern-regex` if metavariables don't bind across dotted paths. The generator's rule
  template can't be finalized until this is verified.
- [ ] Exact placement of `_boundary_check` in `commit_worktree` relative to the code-metrics computation,
  and whether `BoundaryViolationError` should reuse the precise message shape the agent already parses for
  `LintError` (vs. a distinct boundary-violation surface). Leaning: run the check before metrics, reuse the
  `LintError`-style surface so the agent's existing fix loop applies unchanged. (Resolve in plan.)

## Change log

- 2026-06-08: Initial draft (brent-hoover)
- 2026-06-08: Revised ×1 (design-reviewer) — thread `project_path` through `commit_worktree` + both call
  sites (rules live outside the worktree); pin the offline semgrep invocation (`--metrics off`) and define
  the exit-code contract (1=findings→fail, ≥2=error→loud-degrade); make missing module package dir and
  unknown module ids generation-time hard errors (no silent-green); name `sa_mvp.yaml` + its prose catalog
  as the wiring surface; full `BoundariesFile` tool payload incl. `change_log`; soften the semgrep-pattern
  coverage claim to a plan-verified precondition.
- 2026-06-09: Revised ×6 (roborev 397) — module set is now `architecture.yaml` modules ∪ module dirs
  (arch_set_module declares modules without creating dirs, so dir-only enumeration missed them); synced
  Approach §3 + plan to the implemented behavior (missing package dir is not an error).
- 2026-06-09: Revised ×5 (roborev 395, High) — drop the generation-time missing-package-dir hard error.
  Generation runs at arch_finalize before module code is scaffolded, so a missing dir is normal (rule
  matches nothing until code lands); the only real silent-green is a layout/convention mismatch, documented.
- 2026-06-09: Revised ×4 (roborev 392) — relative rule also catches submodule imports
  (`from ..billing.invoices import x`) via metavariable-regex; `generate_boundary_rules` validates +
  compiles all modules before mutating the output dir, so a bad input never leaves the project unenforced.
- 2026-06-09: Revised ×3 (roborev 388 + 390) — internal targets get a separate relative-import deny rule
  scoped to module-root files (`<pkg_dir>*.py`, depth 0); this closes the `from ..billing` bypass without
  false-positiving nested intra-module subpackages (where `..` resolves inside the module). Generator
  hard-errors when a boundaries file's `module` field doesn't match its directory.
- 2026-06-08: Revised ×2 (plan-reviewer feedback) — derive `project_path` from `worktree_path.parents[2]`
  with a layout assertion instead of threading it through `commit_worktree` (keeps the signature + call
  sites unchanged); resolve modules-with-boundaries by globbing `*/boundaries.yaml` (not the
  `contracts.yaml`-keyed `_collect_authored_module_ids`) so a boundaries-only module isn't silently
  skipped.
