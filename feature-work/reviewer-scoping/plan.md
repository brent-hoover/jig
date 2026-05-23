---
title: Reviewer file-scoping and test-reviewer expansion — Implementation Plan
type: plan
status: active
owner: brent
created: 2026-05-22
updated: 2026-05-22  # approved by operator, bumped to active
design: ./design.md
---

# Reviewer file-scoping and test-reviewer expansion — Implementation Plan

## Overview

Build bottom-up: model → shared helpers → MCP tools → catalog guard → role configs → prompts →
routing-layer defence. Each step is self-tested and the next layer composes on the verified previous
one. The test-adequacy prompt expansion lands last so the federation-coverage shift only takes effect
after all the mechanical guards are in place; otherwise we'd briefly have a "test reviewer is the
sole owner of test concerns" promise that the non-test reviewers haven't been silenced from yet.

## Preconditions

- [x] Problem statement approved (2026-05-22).
- [x] Design approved (2026-05-22).
- [ ] Branch `feat/reviewer-scoping` checked out (already created off `origin/develop`).

## Steps

### 1. Add `reads_glob` / `reads_exclude` to `RoleConfig`

**What:** Add two optional `list[str]` fields to `RoleConfig` in `jig/models.py`, both defaulting to
empty lists. Round-trip through `save_role` / `load_role` (existing YAML serialisation in
`jig/persistence.py`).

**Why:** Foundation for every later layer. With empty defaults, no existing role config breaks.

**Verify:** Unit test in `tests/test_models.py` (or `tests/test_role_config.py`) — round-trip a
RoleConfig with and without the new fields through YAML, assert fields land correctly. Run
`uv run pytest tests/test_models.py tests/test_persistence.py -v`.

### 2. Build `jig/scope.py` — glob helpers

**What:** New module with two pure helpers:

```python
def path_in_scope(
    path: str,
    *,
    include: list[str],
    exclude: list[str],
) -> bool: ...

def glob_to_git_pathspec(
    include: list[str],
    exclude: list[str],
) -> list[str]: ...
```

`path_in_scope` uses `pathlib.PurePosixPath.match` against each include pattern (returns True if any
match) AND not against any exclude pattern. Empty `include` means "no scoping" → returns True.

`glob_to_git_pathspec` emits the list of args we pass after `--` to `git diff`: each include glob
verbatim, each exclude glob prefixed with `:!` (git's exclusion pathspec magic).

**Why:** Shared infrastructure used by both MCP tools and the routing-layer defence. Pure functions —
easy to unit-test exhaustively before wiring into anything live.

**Verify:** New `tests/test_scope.py` with at least: in-scope match, exclude-wins-over-include,
multiple include patterns, empty include (no scoping), `**/` wildcards, `.` and `..` rejection in
paths.

### 3. Add `reviewer_get_diff` MCP tool

**What:** Register a new `@tool` in `jig/mcp_server.py` alongside `reviewer_post_comment`, gated on
`reviewer_get_diff` appearing in the role's `allowed_tools`. Implementation shells to
`git diff <base>..HEAD -- <pathspec>` where pathspec comes from `glob_to_git_pathspec`. Returns
`{"diff": str, "scope": list[str]}`. The `base` arg defaults to `JIG_TICKET_BASE` env var when
unset; falls through to `HEAD~1` for manual / dev invocation.

**Why:** Replaces `Bash(git diff*)` as the diff source. Reviewers cannot compose around it.

**Verify:** New tests in `tests/test_reviewer_mcp_tools.py` that build a tmp_path git repo with
content under `src/` and `tests/`, register the tool with a fake `agent_cfg` that has
`reads_glob=["src/**"]`, invoke it, assert the returned diff contains src/ files and excludes
tests/ files.

### 4. Add `reviewer_read_file` MCP tool

**What:** Same pattern as step 3. Args: `{"path": str}`. Validates path via `path_in_scope`,
rejects absolute paths and any path containing `..`, reads from `worktree_path / path`. Returns
`{"content": str}` on success, `{"error": str}` on out-of-scope or refused path.

**Why:** Replaces `Read` for scoped reviewers. With both this and the diff tool, the reviewer has
no path to test-file content.

**Verify:** Add tests to `tests/test_reviewer_mcp_tools.py` — in-scope read returns content,
out-of-scope read returns error with scope info, absolute path rejected, `..` path rejected.

### 5. Catalog-validator guards on `reads_glob`

**What:** Extend `jig/catalog.py:validate_catalog` to enforce: when a role declares non-empty
`reads_glob`, its `allowed_tools` must NOT contain `Read` or any `Bash(git diff*)` variant, and
MUST contain `reviewer_get_diff` + `reviewer_read_file`. Validate each glob entry parses (rough
syntactic check — `pathlib.PurePosixPath("a/b/c").match(pattern)` doesn't raise).

**Why:** Operator config error → loud failure at startup, not silent escape hatches at runtime.

**Verify:** Tests in `tests/test_catalog_validation.py` — write a role config with `reads_glob`
but also `Read` in allowed_tools, expect `CatalogError`. Write a clean config, expect pass.

### 6. Update five non-test reviewer YAML configs

**What:** Edit each of `jig/defaults/roles/reviewer_error_handling.yaml`,
`reviewer_pattern_conformance.yaml`, `reviewer_performance.yaml`, `reviewer_security.yaml`,
`reviewer_architectural.yaml`:

- Add `reads_glob` block (see design.md §Layer 4).
- Add `reads_exclude` block.
- Remove `Read` from `allowed_tools`, add `reviewer_read_file`.
- Remove `Bash(git diff*)` from `allowed_tools`, add `reviewer_get_diff`.
- Add an "## Out of scope" stanza to `phase_prompt` explaining tests/** are not visible (design
  §Layer 5).

**Why:** Activates the mechanical guards for the five federation reviewers.

**Verify:** `validate_catalog` passes (step 5 catches config errors). Spawn one of them in an
integration test (`tests/test_reviewer_scoping_integration.py`) against a tmp project containing
both `src/` and `tests/` files, drive a diff request, assert it returns only `src/` content.

### 7. Update `reviewer_test_adequacy.yaml`

**What:** Add `reads_glob: [tests/**, **/conftest.py, **/test_*.py, **/*_test.py, pyproject.toml]`.
Swap `Read` → `reviewer_read_file`, swap `Bash(git diff*)` → `reviewer_get_diff`. **Do not yet
expand the prompt's What-you-flag content** — that's step 9, deferred until the rest of the
mechanical chain is in place.

**Why:** Symmetrical with step 6. The test reviewer has its own scope and uses the same MCP tools.

**Verify:** `validate_catalog` still passes. Integration test: spawn test-adequacy against the
same tmp project, assert it sees `tests/` content and NOT `src/`.

### 8. Routing-layer defence-in-depth

**What:** Extend `jig/reviewer_routing.py:_route_one` — before computing the target phase, check
the comment's `file` against the issuing reviewer's `reads_glob` via `path_in_scope`. If out of
scope, log a warning and return `(None, "out-of-scope-finding")` so the orchestrator drops the
comment without bouncing the ticket.

**Why:** Catches LLM-hallucinated findings on files the reviewer literally couldn't have read.
Belt-and-braces with the MCP enforcement.

**Verify:** Test in `tests/test_reviewer_routing.py` — synthesise a comment from a non-test
reviewer with `file="tests/test_x.py"`, assert it's dropped and no route returned.

### 9. Expand `reviewer-test-adequacy` prompt to cover consistency

**What:** Edit `reviewer_test_adequacy.yaml`'s `phase_prompt`. Restructure existing "What you
flag" content into two numbered subsections:
- `### Section 1 — Adequacy` (existing content)
- `### Section 2 — Consistency` (new content: fixture/helper duplication that should live in
  conftest; BASE_URL / shared-constant re-definitions; import ordering in test files;
  `@pytest.mark` consistency; parametrize id consistency)

Add a closing instruction: "File findings from BOTH sections before exiting. If you're approaching
turn-budget, file Section 1 findings first."

**Why:** Closes the federation-coverage gap that the mechanical guards opened. Without this, test
consistency goes uncovered entirely — non-test reviewers stop filing on it (good) but test-adequacy
hasn't been told to start (bad).

**Verify:** Manual / eval-driven. The hn-cli RC-9 scenario from the problem statement (duplicate
`_register_all_story_routes`) should be flagged by test-adequacy alone after this change. Run the
hn-cli eval (or a synthetic repro with the same shape) and inspect the resulting findings.

### 10. End-to-end repro test

**What:** Add `tests/test_reviewer_scoping_e2e.py` — synthesise a small project with both `src/`
and `tests/` files containing the RC-9-style duplicate-helper pattern. Spawn the full federation
against it. Assert:
- Pattern-conformance files NO findings on tests/ files.
- Test-adequacy files the duplicate-helper finding.
- Routing layer accepts the test-adequacy finding and routes back to `test`.

**Why:** Pins the integration behaviour described in problem.md §Success criteria. Future
refactors of any single layer that breaks the end-to-end promise fail loudly.

**Verify:** The test itself is the verification.

## Rollback

The new fields default to empty / unscoped, and the new MCP tools are opt-in via `allowed_tools`.
Reverting steps 6-7 (revert the YAML edits) immediately returns reviewers to the pre-change
unscoped behaviour. Steps 1-5 are inert without those YAML changes — they sit dormant.

If a deeper rollback is needed: revert the branch entirely; no schema migrations, no persistent
state changes to undo.

## Out of scope for this plan

- Reducing the federation membership for `small` profile projects.
- Carry-forward closure policy for `notable` findings.
- Forward-walk routing fix (no-op dev re-run after test bounce).
- Per-reviewer divergent scopes — uniform scope across the five non-test reviewers in v1.
- Splitting test-adequacy into two reviewers.
- Tests against the actual hn-cli eval project — the e2e test in step 10 uses a synthetic fixture
  to stay hermetic.

## Change log

- 2026-05-22: Initial draft (brent)
