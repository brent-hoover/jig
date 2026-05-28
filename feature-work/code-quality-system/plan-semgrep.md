---
title: Sub-issue A — Make Semgrep Functional — Implementation Plan
type: plan
status: active
owner: Brent Hoover
created: 2026-05-28
updated: 2026-05-28
design: ./design.md
---

# Make Semgrep Functional Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the canonicalizer's already-plumbed Semgrep path actually run — install the engine, ship a few
real seed rules, run it offline — so a seeded rule fires (and autofixes) on agent output end-to-end.

**Architecture:** #17 already wrote the canonicalizer agent runbook (`jig/defaults/roles/canonicalizer.yaml`)
to run `semgrep --config .jig/rules/semgrep/ --autofix <files>` via bash and record results through the
`log_audit_entry` / `create_canonicalization_issue` MCP tools. The gaps are: `semgrep` isn't installed (not a
dep, not in the Docker image), no rule content ships, and the runbook doesn't force offline/metrics-off. This
plan closes exactly those gaps. The bwrap sandbox binds `/` read-only, so a Docker-installed `semgrep` is
automatically visible to agents (same as `ruff`).

**Tech Stack:** Python 3.12, uv, Semgrep OSS CLI (LGPL-2.1), pytest, Docker.

## Scope (this sub-issue = #109)

In scope: semgrep as a dependency + in the sandbox; 2-3 real seed rules; offline hygiene; an end-to-end test
that a seed rule fires/autofixes via real semgrep.

**Deferred to sub-issue B** (called out so it isn't built here): the universal jig-shipped rule-pack at
`jig/defaults/rules/semgrep/` and extending `list_semgrep_rule_paths` to layer shipped + project rules. For A,
seed rules ship in the **python project template** (`jig/defaults/project_templates/python/.jig/rules/semgrep/`)
so scaffolded projects get them and the runbook's existing `--config .jig/rules/semgrep/` finds them.

## Open scope decisions (confirm before/at start)

- **semgrep as a core dependency** vs. an optional extra. This plan makes it a core dep (the `canonicalize`
  workflow ships by default, so the engine must be present). Semgrep is a heavy install; the alternative is an
  optional dependency group, but then default `uv sync` wouldn't get it and the shipped workflow would break.
  Recommendation: core dep.

## Preconditions

- [x] Design approved and merged (`design.md`, PR #113).
- [x] Working in worktree `.worktrees/code-quality-semgrep` off `origin/develop`.
- [ ] `uv sync` run in the worktree.

## File structure

- `pyproject.toml` — add `semgrep` to `[project].dependencies`.
- `Dockerfile` — add a `pip install semgrep` layer (near the existing `ruff` install).
- `jig/defaults/project_templates/python/.jig/rules/semgrep/error-handling.yml` (new) — detect-only seed rule.
- `jig/defaults/project_templates/python/.jig/rules/semgrep/style.yml` (new) — autofixable seed rules.
- `jig/defaults/roles/canonicalizer.yaml` — add `--metrics off` to the semgrep/deprecation commands.
- `tests/test_semgrep_seed_rules.py` (new) — real-semgrep validation + fire/autofix tests.
- `tests/test_canonicalizer_role_offline.py` (new) — guard that the runbook keeps the offline flags.

---

## Task 1: Add semgrep as a dependency

**Files:**
- Modify: `pyproject.toml` (the `[project] dependencies` array)

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `"semgrep>=1.100"` to the `dependencies` list (place it after `"radon>=6.0.1"`).
Use the latest stable lower bound — check `uv pip index versions semgrep` and pin `>=` the current stable
minor.

- [ ] **Step 2: Sync**

Run: `uv sync`
Expected: resolves and installs semgrep and its transitive deps with no error.

- [ ] **Step 3: Verify the CLI is available**

Run: `uv run semgrep --version`
Expected: prints a version string (e.g. `1.x.y`), exit 0.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(code-quality): add semgrep dependency (#109)"
```

---

## Task 2: Ship seed semgrep rules + prove they fire (TDD)

**Files:**
- Create: `jig/defaults/project_templates/python/.jig/rules/semgrep/style.yml`
- Create: `jig/defaults/project_templates/python/.jig/rules/semgrep/error-handling.yml`
- Test: `tests/test_semgrep_seed_rules.py`

- [ ] **Step 1: Write the failing test**

```python
"""Real-semgrep tests for the shipped seed rules. Semgrep is a project dependency
(like radon/ruff); these run it for real — no mocks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

RULES_DIR = (
    Path(__file__).resolve().parent.parent
    / "jig"
    / "defaults"
    / "project_templates"
    / "python"
    / ".jig"
    / "rules"
    / "semgrep"
)


def _semgrep(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["semgrep", "--metrics", "off", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def test_seed_rules_validate() -> None:
    proc = _semgrep("--validate", "--config", str(RULES_DIR), cwd=RULES_DIR)
    assert proc.returncode == 0, proc.stderr


def test_eq_none_rule_autofixes(tmp_path: Path) -> None:
    target = tmp_path / "m.py"
    target.write_text("def f(x):\n    return x == None\n")
    proc = _semgrep(
        "--config", str(RULES_DIR / "style.yml"), "--autofix", str(target), cwd=tmp_path
    )
    # semgrep exits 1 when findings are present, 0 when none, 2 on fatal error.
    # The meaningful assertion is that the autofix was actually applied.
    assert proc.returncode in (0, 1), proc.stderr
    assert "is None" in target.read_text()
    assert "== None" not in target.read_text()


def test_bare_except_pass_is_detected(tmp_path: Path) -> None:
    target = tmp_path / "e.py"
    target.write_text("def f():\n    try:\n        g()\n    except:\n        pass\n")
    proc = _semgrep(
        "--config", str(RULES_DIR / "error-handling.yml"), "--json", str(target), cwd=tmp_path
    )
    assert proc.returncode in (0, 1), proc.stderr  # 2 = fatal error
    findings = json.loads(proc.stdout)["results"]
    assert any(f["check_id"].endswith("no-swallowed-exception") for f in findings)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_semgrep_seed_rules.py -q`
Expected: FAIL — `RULES_DIR` doesn't exist yet / no rules to validate.

- [ ] **Step 3: Create the autofix rules**

`jig/defaults/project_templates/python/.jig/rules/semgrep/style.yml`:

```yaml
rules:
  - id: use-is-none
    languages: [python]
    severity: WARNING
    message: "Use 'is None' instead of '== None' (identity, not equality)."
    pattern: $X == None
    fix: $X is None
  - id: prefer-logger-warning
    languages: [python]
    severity: WARNING
    message: "logger.warn() is deprecated; use logger.warning()."
    pattern: $LOG.warn($...ARGS)   # capture args so the fix preserves them
    fix: $LOG.warning($...ARGS)
```

- [ ] **Step 4: Create the detect-only rule**

`jig/defaults/project_templates/python/.jig/rules/semgrep/error-handling.yml`:

```yaml
rules:
  - id: no-swallowed-exception
    languages: [python]
    severity: ERROR
    message: "Swallowed exception: a bare 'except: pass' hides errors. Handle or re-raise."
    pattern: |
      try:
          ...
      except:
          pass
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_semgrep_seed_rules.py -q`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add jig/defaults/project_templates/python/.jig/rules/semgrep tests/test_semgrep_seed_rules.py
git commit -m "feat(code-quality): ship seed semgrep rules for the python template (#109)"
```

---

## Task 3: Install semgrep in the Docker image

**Files:**
- Modify: `Dockerfile` (near the `RUN pip install --no-cache-dir ruff` line)

- [ ] **Step 1: Add the install layer**

After the existing ruff install line, add:

```dockerfile
# semgrep (used by the canonicalizer agent for rule-based convention enforcement)
RUN pip install --no-cache-dir semgrep
```

- [ ] **Step 2: Verify it builds and the binary is present in-image**

Run: `docker build -t jig-semgrep-check . && docker run --rm --entrypoint semgrep jig-semgrep-check --version`
Expected: image builds; prints a semgrep version. (`--entrypoint semgrep` is required — the image's
`ENTRYPOINT` is `jig`, so without it the args go to `jig`. This is a heavier step — run once to confirm.)

- [ ] **Step 3: Commit**

```bash
git add Dockerfile
git commit -m "build(code-quality): install semgrep in the container image (#109)"
```

---

## Task 4: Force offline / metrics-off in the canonicalizer runbook (TDD)

**Files:**
- Modify: `jig/defaults/roles/canonicalizer.yaml` (the Step 2 + Step 3 semgrep commands)
- Test: `tests/test_canonicalizer_role_offline.py`

- [ ] **Step 1: Write the failing test**

```python
"""Guard: the canonicalizer runbook must invoke semgrep with metrics off so it
never attempts a network call inside the sandbox."""

from pathlib import Path

import yaml

ROLE = (
    Path(__file__).resolve().parent.parent
    / "jig"
    / "defaults"
    / "roles"
    / "canonicalizer.yaml"
)


def test_runbook_runs_semgrep_offline() -> None:
    prompt = yaml.safe_load(ROLE.read_text())["phase_prompt"]
    # Every semgrep invocation in the runbook carries --metrics off. Match
    # "semgrep" and "--config" independently — after the fix the line reads
    # `semgrep --metrics off --config ...`, so "semgrep --config" is no longer
    # a substring and a combined check would go vacuous.
    for line in prompt.splitlines():
        if "semgrep" in line and "--config" in line:
            assert "--metrics off" in line, f"semgrep call missing --metrics off: {line}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_canonicalizer_role_offline.py -q`
Expected: FAIL — the current runbook commands lack `--metrics off`.

- [ ] **Step 3: Update the runbook commands**

In `jig/defaults/roles/canonicalizer.yaml`, change the two semgrep invocations:

- `semgrep --config .jig/rules/semgrep/ --autofix <CHANGED_FILES>`
  → `semgrep --metrics off --config .jig/rules/semgrep/ --autofix <CHANGED_FILES>`
- `semgrep --config .jig/rules/deprecations.yml --autofix <CHANGED_FILES>`
  → `semgrep --metrics off --config .jig/rules/deprecations.yml --autofix <CHANGED_FILES>`

(Keep the surrounding prose; only the command lines change.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_canonicalizer_role_offline.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/defaults/roles/canonicalizer.yaml tests/test_canonicalizer_role_offline.py
git commit -m "fix(code-quality): run canonicalizer semgrep offline (--metrics off) (#109)"
```

---

## Task 5: Full verification gate

- [ ] **Step 1: Lint + format**

Run: `uv run ruff check jig/ tests/ && uv run ruff format --check $(git diff --name-only origin/develop...HEAD | grep '\.py$')`
Expected: clean on changed files.

- [ ] **Step 2: Full test suite**

Run: `uv run pytest tests/ -q`
Expected: green (baseline ~4267 + the new tests).

- [ ] **Step 3: Manual end-to-end (operator)**

The audit-logging path is agent-driven and not unit-testable. Verify once manually:
1. In a scaffolded python project (or a fixture with `.jig/rules/semgrep/` + a file containing `x == None`),
   run the `canonicalize` workflow.
2. Confirm semgrep autofixes `== None` → `is None` and the canonicalizer calls `log_audit_entry`
   (`rule_source="semgrep"`).
3. Add a bare `except: pass` and confirm a `create_canonicalization_issue` is raised (no autofix).

## Rollback

Pure additive. Revert the branch. The only behavior change to existing code is the runbook `--metrics off`
edit and the new dependency; nothing else is touched.

## Out of scope for this plan

- The universal jig-shipped rule-pack + `list_semgrep_rule_paths` layering (sub-issue B).
- The AI-audit taxonomy rules + `taxonomy.yaml` (sub-issue B).
- Reviewer-facing disposition (sub-issue C).
- Measurement/attribution (sub-issue D).
- **Deprecations path fix (pre-existing #17 gap):** the canonicalizer runbook runs
  `semgrep --config .jig/rules/deprecations.yml`, but that file is modelled as a `deprecations:` manifest that
  needs `DeprecationsConfig.to_semgrep_rules()` conversion before Semgrep can read it. This predates this PR
  (introduced in #17 / PR #20) and fixing it properly (convert to a temp rule file, or switch the format +
  loaders + tests) is its own change. Flagged via roborev #220; to be tracked separately, not fixed in A.

## Change log

- 2026-05-28: Initial draft (Brent Hoover)
- 2026-05-28: Review fixes — corrected semgrep exit-code asserts (1 = findings present, not failure); fixed the
  vacuous offline-guard substring match (`semgrep` + `--config` independently); hoisted `import json` and
  dropped unused `shutil`/`pytest` imports; exact `check_id` match for the swallowed-exception rule
  (Brent Hoover)
- 2026-05-28: roborev (#220/#222) fixes — `prefer-logger-warning` now captures args (`$...ARGS`) so autofix
  preserves them (+ regression test); Docker verify uses `--entrypoint semgrep`; recorded the pre-existing
  deprecations-format gap as out-of-scope (Brent Hoover)
