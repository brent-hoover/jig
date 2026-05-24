---
title: Scaffolding via Templates — Implementation Plan
type: plan
status: draft
owner: brent
created: 2026-05-24
updated: 2026-05-24
design: ./design.md
---

# Scaffolding via Templates — Implementation Plan

## Overview

Two PRs, sequenced:

- **PR 1 — remove `feature-xs`.** Closes the user-facing "no tests on foundation" gap by routing the first feature ticket
  of every project through a workflow with a `test` phase. Ships an alias in `load_workflow` so existing-project tickets
  with `workflow: feature-xs` resolve to `feature-s` instead of erroring.
- **PR 2 — ship `python-cli` template.** Adds the opinionated template + a pytest-collected smoke test that runs each
  bundled template through a real scaffold. SA picks `python-cli` for CLI-shaped briefs via a prompt-level rule.

PR 1 first because it stands alone (no template work required), closes the test-coverage gap immediately, and is a smaller
blast radius if anything goes sideways. PR 2 stacks on develop after PR 1 lands.

## Preconditions

- [x] Problem statement approved (`problem.md`).
- [x] Design approved (`design.md`).
- [ ] Working tree on branch `feat/scaffolding-templates` in worktree `.worktrees/feat-scaffolding-templates`.
- [ ] Baseline `uv run pytest tests/ -q` and `uv run ruff check jig/` pass before any edits.

## Steps

### PR 1 — Remove `feature-xs`

#### 1.1 — Add workflow alias in `load_workflow`

**What:** In `jig/persistence.py`, add a module-level dict `_WORKFLOW_ALIASES = {"feature-xs": "feature-s"}`. In
`load_workflow`, resolve `name` through the alias dict before either file lookup. When an alias fires, log at WARN level once
per process with a structured payload (`{"original": name, "resolved": canonical, "site": "load_workflow"}`) — use a module-
level `set()` of seen aliases to dedupe so we don't log on every ticket lookup. Drop the dedupe state if a test needs
isolation (`pytest` fixture can `_WORKFLOW_ALIASES_LOGGED.clear()`).

**Why:** Lets existing-project tickets with `workflow: feature-xs` resolve to `feature-s` after the workflow file is deleted.

**Verify:**

- New unit test in `tests/test_persistence.py` (or wherever `load_workflow` is tested today): asserts
  `load_workflow(project_path, "feature-xs")` returns the `feature-s` workflow object, and asserts the WARN log fires.
- Second call to the same alias doesn't double-log.

#### 1.2 — Delete `feature-xs.yaml` and update references

**What:**

- Delete `jig/defaults/workflows/feature-xs.yaml`.
- `jig/defaults/profiles/small.yaml`: `default_by_size.xs: feature-s`; remove `feature-xs` from `available`. Update the
  prose comment that mentions xs as "no test phase."
- `jig/defaults/profiles/medium.yaml`: same change; update prose.
- `jig/defaults/work_types/feature.yaml`: `xs: feature-s`; remove the `# implement → validate only` comment.
- `jig/defaults/roles/reviewer_generalist.yaml`: drop `feature-xs` from the workflow list in the role description.
- `jig/reviewers/dispatch.py`: drop `feature-xs` from the comment at line ~106. Confirm there is no active dispatch code
  branching on `feature-xs`; if there is, remove that branch and adjust the test that covered it.
- `jig/defaults/roles/pm.yaml`: rephrase the xs sizing rule (line ~206) so xs no longer implies "skip TDD." New phrasing:
  "xs = small, single-file or near-trivial change. Still goes through the full test → dev → review → validate workflow."

**Why:** Removes the workflow file and every authored reference so the codebase is consistent. Profiles continue to emit
`xs` as a size; the resolution flows through the alias to `feature-s`.

**Verify:**

- `rg "feature-xs"` returns zero matches outside `tests/` (which may reference the deleted name for the alias regression
  test).
- `uv run pytest tests/ -q` still passes — the alias takes care of any test fixture that referenced `feature-xs`. If a test
  was asserting "feature-xs has no test phase" or similar, it gets deleted (that's the property we're removing).
- Manual: `uv run python -c "from jig.persistence import load_workflow; from pathlib import Path; print(load_workflow(Path('/tmp/fake'), 'feature-xs').name)"` prints `feature-s` and logs the WARN.

#### 1.3 — Audit + ensure no dangling profile / sim references

**What:** Run `rg -n "xs.*feature-xs|feature-xs.*xs"` across jig/, tests/, and sim/ scenarios. Anything that still maps xs
→ feature-xs gets updated to xs → feature-s. Re-confirm `jig/sim/driver.py` and `jig/sim/assertions.py` don't hard-code
`feature-xs` (sim is a simulation harness for the orchestrator — broken sim assertions would silently pass tests).

**Why:** The simulation harness is one of the more obscure places `feature-xs` could lurk; missing it produces silent test
drift.

**Verify:** `rg "feature-xs" jig/ tests/` shows only the alias entry in `persistence.py` and tests that exercise the alias.

#### 1.4 — Update jig's own CLAUDE.md / docs if `feature-xs` is referenced

**What:** Grep `CLAUDE.md`, `feature-work/`, and `docs/reference/` for `feature-xs`. Anything that documented "xs skips
tests" gets rewritten or marked superseded.

**Why:** Stale docs become misleading after the workflow is gone.

**Verify:** `rg "feature-xs|xs.*skip.*test|xs.*no test" CLAUDE.md feature-work/ docs/` returns either zero matches or
explicitly-superseded references.

#### 1.5 — Open PR 1

**What:** Standard PR per CLAUDE.md PR conventions. Title: `feat(workflow): remove feature-xs; alias to feature-s`. Problem
/ Fix section references the analyzer eval that surfaced the gap.

**Verify:**

- PR body includes a Manual Test Steps section: `jig init` a throwaway project, confirm the first PM-created feature ticket
  picks `feature-s` workflow.
- CI green.
- Roborev clean (or findings addressed per `/roborev-fix`).

### PR 2 — Ship `python-cli` template

#### 2.1 — Create the template directory

**What:** New directory `jig/defaults/project_templates/python-cli/` containing:

- `template.yaml` — metadata: `description: "Python CLI tool with typer + httpx."`, `language: python`,
  `framework: typer`, `deploy_target: null`, `package_manager: uv`.
- `pyproject.toml` — copies from the `python` template, adds `typer>=0.12`, `httpx>=0.27` to `dependencies`. Includes a
  `[project.scripts]` entry `myproject = "myproject.cli:app"`.
- `README.md` — short CLI-shape stub. Mentions `uv run myproject --help`.
- `.gitignore` — same as `python` template.
- `src/myproject/__init__.py` — empty.
- `src/myproject/__main__.py` — `from .cli import app; app()` so `python -m myproject` works.
- `src/myproject/cli.py` — `typer.Typer()` app with a placeholder `hello` command that echoes a greeting. Wired through
  the transport so the smoke test exercises the dependency-injection seam.
- `src/myproject/transport.py` — `class Transport` that wraps an `httpx.AsyncClient` passed via constructor. Default
  factory creates one with sensible timeouts; tests construct with their own client.
- `tests/__init__.py` — empty.
- `tests/test_smoke.py` — asserts `myproject --help` runs cleanly (exit 0, stdout includes "Usage:"). Uses
  `typer.testing.CliRunner` so no subprocess needed inside the test itself.

**Why:** This is the opinionated foundation that hn-cli-shaped projects can use unchanged.

**Verify:**

- Manually scaffold into a tmp dir: `python -c "from jig.init_workflow import _apply_template_files; from pathlib import
  Path; _apply_template_files(template_name='python-cli', dest=Path('/tmp/scaffold-test'), project_name='scaffold_test')"`.
- `cd /tmp/scaffold-test && uv sync && uv run pytest -q` passes.
- `uv run scaffold_test --help` prints typer help text.

#### 2.2 — Add `tests/test_template_smoke.py`

**What:** Pytest-collected test that:

1. Discovers each subdirectory under `jig/defaults/project_templates/`.
2. For each: creates a tmp dir, calls `_apply_template_files(template_name=<name>, dest=tmp_dir, project_name="scaffold_test")`.
3. Subprocess: `uv sync --directory tmp_dir` then `uv run --directory tmp_dir pytest -q`.
4. Asserts exit 0 and a non-empty stdout that mentions "passed."

**Why:** CI catches a broken template before it reaches an `jig init` run.

**Verify:**

- The test runs locally (`uv run pytest tests/test_template_smoke.py -v`) and passes for both `python` and `python-cli`.
- Deliberately break `python-cli/src/myproject/cli.py` (e.g. syntax error) — test fails with a clear error.

#### 2.3 — Add `collect_ignore` so pytest doesn't double-collect template tests

**What:** In `tests/conftest.py` (create if absent), add:

```python
collect_ignore_glob = ["../jig/defaults/project_templates/*/tests/*"]
```

Or, equivalently, in `pyproject.toml` `[tool.pytest.ini_options]` add `norecursedirs = ["jig/defaults/project_templates"]`.
Prefer the pytest.ini option — single config site, doesn't depend on conftest discovery order.

**Why:** Without it, the template's `tests/test_smoke.py` gets collected by the main `pytest tests/` run with the
unsubstituted `myproject` name and fails to import.

**Verify:** `uv run pytest tests/ -v --collect-only` shows the template smoke tests are NOT in the collected list. The
`tests/test_template_smoke.py` IS collected and runs the scaffold flow.

#### 2.4 — Update SA role for shape detection

**What:** Append to `jig/defaults/roles/sa.yaml` `phase_prompt`:

```
## Template selection

If the brief implies a command-line tool (mentions "CLI," "command,"
uses imperative subcommands like "search," "filter," "list," or
describes input flags and stdout/file output as the primary interface),
pick `python-cli`. Otherwise pick `python`. When uncertain, prefer
`python` and explain in the rationale.
```

**Why:** SA needs explicit guidance for the new choice. No new tool calls.

**Verify:**

- Replay the hn-cli brief through `run_sa_conversation` (manual eval, not automated). SA picks `python-cli` and explains
  why in the proposal rationale.
- A non-CLI brief (something the user picks for a smoke check) still picks `python`.

#### 2.5 — Update docs for the new template

**What:** Brief mention in `docs/reference/` or wherever templates are documented (search first; if no existing template
doc, skip this step).

**Why:** Discoverability.

**Verify:** `rg "python-cli|project_templates" docs/` shows the new template documented if a docs page exists.

#### 2.6 — Open PR 2

**What:** Standard PR. Title: `feat(templates): ship python-cli template + smoke-test loop`. Problem / Fix references
issue #83 and the analyzer eval. Manual Test Steps: `jig init` an hn-cli-shaped brief, confirm scaffolding produces the
typer entry + transport + smoke test, and the first PM-created ticket is a real feature ticket (not a shell-wiring one).

**Verify:** CI green. Roborev clean. Re-run hn-cli eval if practical and confirm no `feat(skeleton)`-style commit appears
on a feature ticket.

## Rollback

**PR 1:** Revert. The deleted `feature-xs.yaml` was only referenced by the profile files and a couple of comment lines —
nothing structural. The alias is additive and safe to keep even if we revert the workflow deletion (it just becomes a
redundant identity-like mapping that fires for no one).

**PR 2:** Revert. The new template is purely additive — `python` is unchanged. SA's prompt addition is a single section;
removing it returns SA to the pre-change behavior. `tests/test_template_smoke.py` is additive; removing it loses CI
coverage but breaks nothing else.

If the analyzer reveals after either PR lands that the change introduced quality regressions, revert and reopen the
relevant issue rather than patching forward — both PRs are small enough that revert is cheap.

## Out of scope for this plan

- Building `python-web-api`, `python-library`, or any template beyond `python-cli`. Defer per problem.md's incremental
  accretion policy.
- Removing the workflow alias. The alias stays until we have signal that no live project references `feature-xs`.
- Migrating `_apply_template_files` to cookiecutter or Copier. Rejected in design.
- Touching the `sa_v2` / `sa_mvp` role variants. PR 2's SA prompt edit lands on `sa.yaml` only; the v2 variants get the
  same treatment if/when they become the active SA, in a separate PR.
- Changing the PM's sizing logic beyond the `pm.yaml` comment rephrase in PR 1.

## Change log

- 2026-05-24: Initial draft (brent)
