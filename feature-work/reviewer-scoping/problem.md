---
title: Reviewer file-scoping and test-reviewer expansion — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-22
updated: 2026-05-22  # bumped after roborev #106 review
---

# Reviewer file-scoping and test-reviewer expansion — Problem Statement

## Context

The review federation runs five reviewer agents in parallel
(`reviewer-error-handling`, `reviewer-performance`, `reviewer-security`,
`reviewer-pattern-conformance`, `reviewer-architectural`) plus a sixth
(`reviewer-test-adequacy`) that gates the test phase. Each reviewer
receives the full ticket diff and is free to flag findings on any
file the diff touches. Routing to the next phase is determined by
matching the flagged file against each phase's declared `writes`
glob.

In a recent hn-cli run, this produced a chain of pathological
behaviour. `reviewer-pattern-conformance` filed a finding against
`tests/test_api.py` (duplicate `_register_all_story_routes` helper —
RC-9, accurate observation, not a false positive). The orchestrator
routed back to the `test` phase via writes-glob match. Test phase
fixed the duplication by consolidating the helper into `conftest.py`.
`reviewer-test-adequacy` then passed, and the orchestrator advanced
forward through `implement` (= dev, which made a 22-turn no-op
commit because nothing needed to change) and back into the full
5-reviewer federation. Cost: ~$1 plus ~10 minutes of wall time on
work that produced zero net code change.

The trigger was that a non-test reviewer raised a test-side finding
at all. `reviewer-test-adequacy` saw the same test code on the same
diff and chose not to flag it. The two reviewers disagree on what
quality bar applies to test files, and the federation has no
arbitrator. The path of least resistance for the orchestrator is to
believe both, which leads to spurious bouncing.

## Problem

Non-test reviewers have no declared file scope. Their role configs
(`jig/defaults/roles/reviewer_*.yaml`) say what kinds of findings
they raise (error handling, pattern conformance, security, etc.)
but say nothing about which files they're entitled to raise them
against. They read the full diff handed in by the orchestrator —
including `tests/**` — and apply their mandate uniformly.

This produces three concrete failure modes:

1. **Test-side findings from non-test reviewers route back to the
   test phase**, which after fix routes forward through `dev`
   (which has nothing to do) and back into the full federation.
   Observed cost on hn-cli ticket 8e85eb9d: one no-op dev run plus
   one full re-federation = ~$1 wasted.

2. **Reviewer disagreement on test quality.** `reviewer-test-adequacy`
   is the only reviewer with a test-specific prompt and contract;
   its silence on a test file should be authoritative. Today,
   another reviewer's louder opinion overrides it implicitly via
   the routing layer.

3. **Wasted probing turns.** Even when a reviewer doesn't end up
   filing a finding on test files, it may spend turns reading them
   to decide. With five reviewers per round and four rounds on a
   contested ticket, this compounds.

The mirror of (1) also exists: nothing prevents
`reviewer-test-adequacy` from filing a finding on `src/**` files,
even though source quality is the other reviewers' purview. Same
class of bug, less commonly triggered.

## Simplest possible solution

Add a `reads_glob` field to each reviewer's role config. The
orchestrator filters the diff (and the files made readable to the
reviewer) by that glob before spawn.

The scope is an **include-set**, not just `src/**`, because
implementation-owned non-test files (`pyproject.toml`, `uv.lock`,
`Dockerfile`, `*.cfg`, top-level scripts) legitimately fall under
security / architecture / performance / pattern-conformance
review. Non-test reviewers get something like:

```
reads_glob:
  - "src/**"
  - "jig/**"          # this repo's package layout
  - "pyproject.toml"
  - "uv.lock"
  - "Dockerfile"
  - "scripts/**"
  - "docs/**"
exclude:
  - "tests/**"
  - "conftest.py"
```

The test reviewer gets:

```
reads_glob:
  - "tests/**"
  - "conftest.py"
  - "pyproject.toml"   # for pytest config + dev deps
```

A reviewer literally cannot see files outside its scope, so it
cannot file findings on them.

**The orchestrator becomes the sole diff source.** Diff filtering
at spawn time is meaningless if reviewers can shell out to
`git diff main..HEAD` themselves and see everything. Each reviewer
role's `allowed_tools` drops the unrestricted `Bash(git diff*)`
entry. The orchestrator (or the per-agent MCP server) exposes a
single `reviewer_get_diff` tool that returns the diff already
scoped to the reviewer's `reads_glob`. Same applies to `Read` —
non-test reviewers cannot Read paths under `tests/**`.

Reinforce in the role prompts: "Files outside `reads_glob` are
filtered out of your input. There is no git-diff shell command
available — use the `reviewer_get_diff` tool instead. Do not
spend turns probing for files outside your scope; they're owned
by another reviewer." This saves probing budget on top of the
mechanical guard.

Expand `reviewer-test-adequacy`'s prompt to cover the test-quality
ground the other reviewers used to opportunistically cover:
fixture duplication, conftest hygiene, import ordering, mock
helper consistency. Single prompt, two named sections (Adequacy +
Consistency). No second agent spawn — same context window, same
cost as today.

## Complications considered

- **Scale**: N/A — the number of reviewers is fixed per workflow
  and the file-glob check runs once per spawn. Filtering scales
  with diff size, which is bounded.

- **Concurrency**: N/A — reviewer spawns are already
  per-ticket-and-cycle. No shared mutable state changes here.

- **Failure modes**:
  - Misconfigured `reads_glob` (typo, missing glob) → reviewer sees
    empty diff → emits zero findings. Fail-soft, surfaces as
    "reviewer had nothing to say" in the thread. Not silently
    wrong, just under-reviewed. Acceptable.
  - Project with non-standard layout (e.g. `lib/` instead of
    `src/`, monorepo with multiple package roots) → shipped
    defaults wrong. Operators override `reads_glob` per project in
    `.jig/roles/reviewer-*.yaml`. Documented in role.yaml comments.
  - Reviewer routing computes "owning phase" via writes-glob (see
    `reviewer_routing._route_one`). If a finding's file isn't in
    ANY phase's writes glob (e.g. operator-authored conftest in a
    weird location), routing falls back to "unowned-finding →
    last dev phase". That fallback still works after this change.

- **Cross-cutting policies**: N/A — touches no PII, auth, secrets,
  or audit paths. Pure routing / context-filtering change.

- **Cost**: changes intend to *reduce* cost (eliminate redundant
  dev-runs + federation rounds triggered by cross-scope findings).
  Single-prompt expansion of test-adequacy doesn't double its
  cost; turn count may grow ~10–20% from the added consistency
  section.

- **Backwards compatibility**: shipped role configs change; project
  overrides at `.jig/roles/reviewer-*.yaml` still win. Existing
  in-flight tickets see the new behaviour on next reviewer spawn.
  No migration of stored state needed (no comments are rewritten).

## Constraints

- Role configs are YAML at `jig/defaults/roles/` with an existing
  schema (`RoleConfig`). New fields must be optional with sensible
  defaults so legacy operator configs don't break.

- Reviewer prompt edits must preserve the existing federation's
  blocking-finding contract (severity, type, cycle tracking,
  carry-forward). Test-adequacy's expanded mandate must still
  emit findings via `reviewer_post_comment` so they flow through
  the same routing and cycle-tracking machinery.

- Tool restrictions (e.g. `Read(src/**)` instead of bare `Read`)
  must work with the Claude Code SDK's `allowed_tools` schema.
  Confirm the SDK supports path-pattern restriction on `Read`
  before relying on it; if it doesn't, the orchestrator-side diff
  filter is the only enforcement mechanism and the prompt does
  the rest.

## Requirements

- Each reviewer role config carries a `reads_glob` (include-set)
  and an optional `exclude` (exclude-set). Non-test reviewers
  include `src/**` and implementation-owned non-test paths
  (`pyproject.toml`, `uv.lock`, `Dockerfile`, `scripts/**`,
  `docs/**`) and exclude `tests/**` + `conftest.py`. The test
  reviewer includes `tests/**` and `conftest.py` (plus
  `pyproject.toml` for pytest config visibility).

- The orchestrator becomes the **sole diff source** for **every**
  scoped reviewer — including `reviewer-test-adequacy` (its
  scoped reads_glob is symmetric: tests-only means it must not be
  able to ``git diff`` the src side either). All scoped reviewer
  roles drop unrestricted `Bash(git diff*)` from `allowed_tools`.
  The orchestrator's per-agent MCP server exposes
  ``reviewer_get_diff`` that returns the diff pre-filtered by the
  reviewer's ``reads_glob``. Without this, an unscoped
  ``git diff main..HEAD`` still exposes every file regardless of
  the orchestrator-side filter.

- `Read` access is similarly path-restricted. Non-test reviewers
  cannot read files under `tests/**`. Determines whether to use
  the SDK's `allowed_tools` path-pattern syntax (if supported) or
  a per-agent MCP `reviewer_read_file` that enforces scope.

- A reviewer that nonetheless attempts to file a finding outside
  its scope is rejected at the routing layer (the finding's file
  doesn't match any phase the reviewer is allowed to influence —
  treat the finding as a soft-error and log it; do not let it
  bounce the ticket).

- `reviewer-test-adequacy`'s prompt covers both test adequacy
  (AC-driven coverage gaps, mock correctness) AND test consistency
  (fixture duplication, conftest hygiene, import ordering, helper
  naming) in clearly-named sections.

- After this change, the hn-cli RC-9 scenario does not repeat: a
  fixture-duplication issue in tests is either flagged by
  test-adequacy alone (legitimate) or not flagged at all
  (acceptable per its quality bar); pattern-conformance never sees
  the test files via the orchestrator-served diff, MCP-served
  Read, OR any remaining Bash escape hatch.

## Non-goals

- **Not changing the bounce-then-walk-forward routing.** That's a
  separate problem (when a test-only fix bounces, the orchestrator
  walks forward through dev rather than returning to the bouncing
  reviewer). This problem scopes the *file* boundaries, not the
  *phase* boundaries. The two changes compound but are
  independent.

- **Not adding new reviewer roles.** No "reviewer-test-consistency"
  spawn; the existing test-adequacy reviewer absorbs the work.

- **Not changing the federation membership.** Five non-test
  reviewers stay five. The `small` profile's federation over-spec
  (analyzer's rec #6) is a separate concern.

- **Not pre-scanning the diff to decide which reviewers to spawn.**
  Federation membership is workflow-declared; if a workflow lists
  reviewer-security, we spawn it even if the diff has no
  security-relevant changes. (Spending the spawn cost is the
  existing contract; this work doesn't alter that.)

- **Not building generic per-tool path restrictions** as a feature.
  If `Read(src/**)`-style tool restriction is straightforward in
  the SDK, use it; if not, lean on the orchestrator-side diff
  filter and don't build new SDK glue for this alone.

## Success criteria

- Replaying the hn-cli RC-9 scenario (or a synthetic reproduction):
  no non-test reviewer files findings on `tests/**` files. The
  no-op dev run and subsequent full federation re-round do not
  occur. Wall time on ticket 8e85eb9d drops by ~10 minutes;
  cost drops by ~$1.

- `reviewer-test-adequacy` continues to catch the original
  fixture-duplication-class issue (consolidating
  `_register_all_story_routes` into `conftest.py` is a legitimate
  finding it should be able to make).

- No regression in non-test reviewers' ability to flag genuine
  src/ findings — RC-6 (bare except), RC-14 (non-injectable
  client), RC-7 (missing debug log) all still surface from the
  same reviewers.

- New `reads_glob` field documented in `RoleConfig` docstring and
  in `jig/defaults/roles/README.md` (or wherever role conventions
  live).

## Open questions

- [ ] Does the Claude Code SDK's `allowed_tools` support
  path-pattern restriction on `Read` (e.g. `Read(src/**)`)?
  If yes, use it as the enforcement layer for `Read`. If no,
  introduce an MCP `reviewer_read_file` tool that enforces
  scope and drop the unrestricted `Read` entry from reviewer
  role configs.

- [ ] Diff tooling: confirm we want a new
  `reviewer_get_diff` MCP tool (returns scoped diff) rather
  than `Bash(git diff -- <paths>)`. The MCP approach is more
  robust (can't be composed around); the Bash form is lighter
  but trusts the reviewer to use the right path-spec.

- [ ] Where does the diff filter live — in `prompt_builder.py`
  (alongside context resolution) or in the reviewer-spawning
  path in `orchestrator.py`? Probably `prompt_builder` since
  context already routes through there, but the MCP tool would
  call into a shared helper either way.

- [ ] Exact include-set for each non-test reviewer. The list in
  "Simplest possible solution" is a starting point; finalise
  during design. Possible per-reviewer divergence: security
  may want `Dockerfile` + `uv.lock`; architectural may want
  `docs/architecture/**`; performance may not need either.

- [ ] Should the test-adequacy prompt's new Consistency section
  be a numbered subsection within "What you flag," or a peer
  section to the existing top-level structure? Style call for
  the design doc.

## Change log

- 2026-05-22: Initial draft (brent)
- 2026-05-22: Tighten enforcement after roborev #106 — orchestrator
  becomes sole diff source, drop `Bash(git diff*)` from non-test
  reviewers, switch to include-set scope so non-test
  implementation-owned files (pyproject, lockfile, Dockerfile,
  etc.) remain in review coverage. (brent)
- 2026-05-22: Clarify after roborev #109 — sole-diff-source rule
  applies to EVERY scoped reviewer, not just non-test ones.
  reviewer-test-adequacy is scoped too and drops
  ``Bash(git diff*)`` symmetrically. (brent)
