---
title: Sub-issue B — AI-Audit Taxonomy Pack + Manifest + Ruff Enablement — Implementation Plan
type: plan
status: active
owner: Brent Hoover
created: 2026-05-29
updated: 2026-05-30
design: ./design.md
---

# AI-Audit Taxonomy Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Catalog all 24 AI-shaped defect patterns in a jig-shipped `taxonomy.yaml` manifest, and compute
deterministic `TaxonomyHit`s from a jig-owned curated ruff pass over changed files, mapped to taxonomy IDs.

**Architecture:** The taxonomy splits **10 deterministic (all ruff) / 14 judgment** (verified against ruff
`rule`). A manifest (`jig/code_quality/taxonomy.yaml`) is the single source of truth: each entry carries
`detection` (`ruff` with a `ref` code, or `judgment`), an `owning_reviewer`, and a `cue`. A loader derives the
curated `--select` list from the ruff entries; `compute_change_metrics` (shipped in A, `jig/code_metrics.py`)
runs a second ruff pass with that select list over the changed files and maps each finding's `code` → a
`TaxonomyHit` via the manifest. Hits attach to `ChangeMetrics`. The 14 judgment entries are catalogued here for
sub-issue C to surface into reviewer prompts; measurement (sub-issue D) reads the hits.

> **No semgrep.** The design assumed semgrep would carry cue-based patterns, but every deterministic taxonomy
> pattern maps to a ruff rule — so B is "curated ruff + manifest," not a semgrep rule-pack. Semgrep (from A)
> remains for the canonicalizer's project-specific conventions/deprecations.

**Resolves design open question:** extend `ChangeMetrics` with a `taxonomy_hits` field (frozen-compatible,
default empty) rather than introducing a `QualitySignal` wrapper.

**Tech Stack:** Python 3.12, pydantic v2, ruff (already a dep), pytest.

## Scope (sub-issue B = #110)

In: the manifest (all 24), its loader + validation, `TaxonomyHit`, the ruff taxonomy scan, and wiring hits
into `ChangeMetrics`. Out: surfacing hits/judgment checklists into reviewer prompts (C); measurement/audit (D).

## File structure

- `jig/code_quality/__init__.py` (new) — package.
- `jig/code_quality/taxonomy.yaml` (new) — the 24-entry manifest, jig-shipped.
- `jig/code_quality/taxonomy.py` (new) — `TaxonomyEntry`, `TaxonomyHit`, `load_taxonomy()`,
  `taxonomy_ruff_select()`, `scan_taxonomy()`.
- `jig/code_metrics.py` (modify) — add `taxonomy_hits` to `ChangeMetrics`; run the scan in
  `compute_change_metrics`.
- `tests/test_taxonomy_manifest.py` (new), `tests/test_taxonomy_scan.py` (new),
  `tests/test_code_metrics.py` (modify — assert `taxonomy_hits` present).

## Owning-reviewer policy (per design §4)

`error-handling → reviewer-error-handling`; `security → reviewer-security`; `testing → reviewer-test-adequacy`;
**all other categories → `reviewer-pattern-conformance`** (catch-all). Every value must be in
`known_llm_reviewer_ids()`.

---

## Task 1: Taxonomy manifest + loader (TDD)

**Files:**
- Create: `jig/code_quality/__init__.py` (empty), `jig/code_quality/taxonomy.yaml`, `jig/code_quality/taxonomy.py`
- Test: `tests/test_taxonomy_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from jig.code_quality.taxonomy import load_taxonomy
from jig.reviewers.dispatch import known_llm_reviewer_ids

VALID_KINDS = {"ruff", "judgment"}


def test_manifest_has_24_entries() -> None:
    entries = load_taxonomy()
    assert len(entries) == 24
    assert len({e.id for e in entries}) == 24  # unique ids


def test_detection_kinds_and_refs() -> None:
    for e in load_taxonomy():
        assert e.detection.kind in VALID_KINDS
        if e.detection.kind == "ruff":
            assert e.detection.ref, f"{e.id}: ruff entry needs a rule code"
        else:
            assert e.detection.ref is None


def test_owning_reviewers_are_known() -> None:
    known = known_llm_reviewer_ids()
    for e in load_taxonomy():
        assert e.owning_reviewer in known, f"{e.id}: unknown reviewer {e.owning_reviewer}"


def test_ten_ruff_entries() -> None:
    ruff = [e for e in load_taxonomy() if e.detection.kind == "ruff"]
    assert len(ruff) == 10
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_taxonomy_manifest.py -q`
Expected: FAIL — `jig.code_quality.taxonomy` does not exist.

- [ ] **Step 3: Create the manifest**

`jig/code_quality/taxonomy.yaml` (ruff codes verified via `ruff rule <code>`):

```yaml
- {id: TAX-ERR-001, category: error-handling, title: swallowed exceptions,
   detection: {kind: ruff, ref: BLE001}, owning_reviewer: reviewer-error-handling,
   cue: "except Exception: pass / bare except with no re-raise"}
- {id: TAX-ERR-002, category: error-handling, title: inconsistent error handling,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-error-handling,
   cue: "mixed exception strategies across the same module"}
- {id: TAX-ERR-003, category: error-handling, title: brittle error detection,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-error-handling,
   cue: "matching exceptions by string message"}
- {id: TAX-SEC-001, category: security, title: string-built SQL,
   detection: {kind: ruff, ref: S608}, owning_reviewer: reviewer-security,
   cue: "f-string / concatenation building SQL"}
- {id: TAX-SEC-002, category: security, title: shell=True subprocess,
   detection: {kind: ruff, ref: S602}, owning_reviewer: reviewer-security,
   cue: "subprocess(..., shell=True)"}
- {id: TAX-SEC-003, category: security, title: tarfile extractall without filter,
   detection: {kind: ruff, ref: S202}, owning_reviewer: reviewer-security,
   cue: ".extractall() with no members filter"}
- {id: TAX-REL-001, category: reliability, title: missing network timeout,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "requests/httpx call without timeout="}
- {id: TAX-REL-002, category: reliability, title: resource leak (no context manager),
   detection: {kind: ruff, ref: SIM115}, owning_reviewer: reviewer-pattern-conformance,
   cue: "open() not used as a context manager"}
- {id: TAX-ASYNC-001, category: async, title: async-await mismatch,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "calling an async function without await"}
- {id: TAX-ASYNC-002, category: async, title: sleep-based synchronization,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "time.sleep()/asyncio.sleep() used to coordinate tasks"}
- {id: TAX-OBS-001, category: observability, title: print instead of logging,
   detection: {kind: ruff, ref: T201}, owning_reviewer: reviewer-pattern-conformance,
   cue: "print() in library/non-entrypoint code"}
- {id: TAX-OBS-002, category: observability, title: f-string in logger call,
   detection: {kind: ruff, ref: G004}, owning_reviewer: reviewer-pattern-conformance,
   cue: 'logger.info(f"...") instead of %-args'}
- {id: TAX-CF-001, category: control-flow, title: off-by-one,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "range(len(x)) vs range(len(x)-1) / boundary indices"}
- {id: TAX-CF-002, category: control-flow, title: swapped arguments,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "positional args passed in the wrong order"}
- {id: TAX-STRUCT-001, category: structure, title: near-identical siblings,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "duplicated near-identical functions/blocks"}
- {id: TAX-STRUCT-002, category: structure, title: unjustified lazy import,
   detection: {kind: ruff, ref: PLC0415}, owning_reviewer: reviewer-pattern-conformance,
   cue: "import inside a function with no stated reason"}
- {id: TAX-LANG-001, category: language-pitfall, title: mutable default arguments,
   detection: {kind: ruff, ref: B006}, owning_reviewer: reviewer-pattern-conformance,
   cue: "def f(x=[]) / def f(x={})"}
- {id: TAX-LANG-002, category: language-pitfall, title: assert for runtime validation,
   detection: {kind: ruff, ref: S101}, owning_reviewer: reviewer-pattern-conformance,
   cue: "assert used to validate runtime input"}
- {id: TAX-CFG-001, category: configuration, title: hardcoded config values,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "magic hosts/ports/paths instead of config"}
- {id: TAX-CONS-001, category: consistency, title: convention drift,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "naming/style inconsistent with the surrounding code"}
- {id: TAX-DOC-001, category: documentation, title: narrating comments,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "comments restating what the code already says"}
- {id: TAX-TEST-001, category: testing, title: weak test assertion,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-test-adequacy,
   cue: "assert truthy / asserting on shape not behavior"}
- {id: TAX-DEF-001, category: defensive-programming, title: unreachable defensive guard,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "guard that prior code makes impossible"}
- {id: TAX-LIB-001, category: library-usage, title: wrong tool for the job,
   detection: {kind: judgment, ref: null}, owning_reviewer: reviewer-pattern-conformance,
   cue: "hand-rolled logic where a stdlib/dep fits"}
```

- [ ] **Step 4: Create the loader + models**

`jig/code_quality/taxonomy.py`:

```python
"""AI-audit taxonomy manifest: the single source mapping each defect pattern to
its detection (ruff rule or judgment) and owning reviewer."""

from __future__ import annotations

from functools import cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class Detection(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["ruff", "judgment"]
    ref: str | None = None


class TaxonomyEntry(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    category: str
    title: str
    detection: Detection
    owning_reviewer: str
    cue: str


@cache
def load_taxonomy() -> tuple[TaxonomyEntry, ...]:
    text = resources.files("jig.code_quality").joinpath("taxonomy.yaml").read_text()
    raw = yaml.safe_load(text)
    return tuple(TaxonomyEntry.model_validate(item) for item in raw)


def taxonomy_ruff_select() -> list[str]:
    """The curated ruff --select list: every ruff rule the taxonomy maps to."""
    return sorted(
        {e.detection.ref for e in load_taxonomy() if e.detection.kind == "ruff" and e.detection.ref}
    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_taxonomy_manifest.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add jig/code_quality/__init__.py jig/code_quality/taxonomy.yaml jig/code_quality/taxonomy.py tests/test_taxonomy_manifest.py
git commit -m "feat(code-quality): add AI-audit taxonomy manifest + loader (#110)"
```

---

## Task 2: Taxonomy ruff scan → TaxonomyHit (TDD)

**Files:**
- Modify: `jig/code_quality/taxonomy.py` (add `TaxonomyHit`, `scan_taxonomy`)
- Test: `tests/test_taxonomy_scan.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from pathlib import Path

from jig.code_quality.taxonomy import scan_taxonomy


def test_scan_maps_ruff_findings_to_taxonomy_ids(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text(
        "def g(x=[]):\n"          # B006  -> TAX-LANG-001
        "    print('hi')\n"        # T201  -> TAX-OBS-001
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"  # BLE001 -> TAX-ERR-001
        "        pass\n"
    )
    hits = scan_taxonomy(tmp_path, [f])
    ids = {h.id for h in hits}
    assert "TAX-LANG-001" in ids
    assert "TAX-OBS-001" in ids
    assert "TAX-ERR-001" in ids
    for h in hits:
        assert h.file.endswith("bad.py")
        assert h.line > 0
        assert h.reviewer  # owning reviewer carried through


def test_scan_clean_file_no_hits(tmp_path: Path) -> None:
    f = tmp_path / "ok.py"
    f.write_text("def g(x: int) -> int:\n    return x + 1\n")
    assert scan_taxonomy(tmp_path, [f]) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_taxonomy_scan.py -q`
Expected: FAIL — `scan_taxonomy` not defined.

- [ ] **Step 3: Implement `TaxonomyHit` + `scan_taxonomy`**

Add to `jig/code_quality/taxonomy.py` (module-level imports: `import json`, `import logging`,
`import subprocess`, `from pathlib import Path`):

```python
import json
import logging
import subprocess
from pathlib import Path

_logger = logging.getLogger(__name__)


class TaxonomyHit(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    category: str
    file: str
    line: int
    reviewer: str


def scan_taxonomy(worktree_path: Path, py_files: list[Path]) -> list[TaxonomyHit]:
    """Run the curated taxonomy ruff pass over ``py_files`` and map findings to hits.

    Synchronous wrapper; uses ruff's JSON output. A ruff/tooling failure degrades
    to an empty list (signal-only — never raises into the caller)."""
    files = [str(p) for p in py_files if p.suffix == ".py" and p.is_file()]
    if not files:
        return []
    select = taxonomy_ruff_select()
    by_code = {
        e.detection.ref: e for e in load_taxonomy() if e.detection.kind == "ruff"
    }
    try:
        proc = subprocess.run(
            ["ruff", "check", "--select", ",".join(select), "--output-format=json", *files],
            cwd=worktree_path, capture_output=True, text=True,
        )
        raw = proc.stdout.strip()
        findings = json.loads(raw) if raw else []
    except (OSError, ValueError):
        _logger.warning("taxonomy scan failed for %s; no hits", worktree_path, exc_info=True)
        return []
    hits: list[TaxonomyHit] = []
    for fnd in findings:
        entry = by_code.get(fnd.get("code"))
        if entry is None:
            continue
        loc = fnd.get("location", {})
        hits.append(TaxonomyHit(
            id=entry.id, category=entry.category,
            file=fnd.get("filename", ""), line=int(loc.get("row", 0) or 0),
            reviewer=entry.owning_reviewer,
        ))
    return hits
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_taxonomy_scan.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/code_quality/taxonomy.py tests/test_taxonomy_scan.py
git commit -m "feat(code-quality): scan changed files for taxonomy ruff hits (#110)"
```

---

## Task 3: Wire `taxonomy_hits` into `ChangeMetrics` (TDD)

**Files:**
- Modify: `jig/code_metrics.py` (add `taxonomy_hits` field; call `scan_taxonomy` in `compute_change_metrics`)
- Test: `tests/test_code_metrics.py` (add one case)

- [ ] **Step 1: Write the failing test** (append to `tests/test_code_metrics.py`)

```python
async def test_change_metrics_includes_taxonomy_hits(base_repo: Path) -> None:
    (base_repo / "feature.py").write_text("def g(x=[]):\n    return x\n")  # B006
    m = await compute_change_metrics(base_repo, base_ref="main")
    assert any(h.id == "TAX-LANG-001" for h in m.taxonomy_hits)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_code_metrics.py::test_change_metrics_includes_taxonomy_hits -q`
Expected: FAIL — `ChangeMetrics` has no `taxonomy_hits`.

- [ ] **Step 3: Add the field + call the scan**

In `jig/code_metrics.py`:
- Import: `from jig.code_quality.taxonomy import TaxonomyHit, scan_taxonomy`.
- Add to `ChangeMetrics`: `taxonomy_hits: tuple[TaxonomyHit, ...] = ()` (frozen-compatible default).
- In `compute_change_metrics`, after collecting `on_disk` files, compute
  `taxonomy_hits = tuple(scan_taxonomy(worktree_path, [worktree_path / p for p in on_disk]))`
  and pass it into the returned `ChangeMetrics`. Keep it inside the existing `try` so a failure still degrades
  to empty metrics.
- `_EMPTY` keeps the default `taxonomy_hits=()`.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_code_metrics.py -q`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add jig/code_metrics.py tests/test_code_metrics.py
git commit -m "feat(code-quality): attach taxonomy hits to ChangeMetrics (#110)"
```

---

## Task 4: Full verification gate

- [ ] **Step 1: Lint + format** — `uv run ruff check jig/ tests/` and `ruff format --check` on changed files.
- [ ] **Step 2: Full suite** — `uv run pytest tests/ -q` (green; baseline 4274 + new tests).
- [ ] **Step 3: Manual sanity** — `uv run python -c "from jig.code_quality.taxonomy import taxonomy_ruff_select; print(taxonomy_ruff_select())"` prints the 10 codes.

## Rollback

Additive: a new package + a `ChangeMetrics` field with a default. Revert the branch. No data migration.

## Out of scope for this plan

- Surfacing hits / judgment checklists into reviewer prompts (sub-issue C).
- Measurement/attribution over AuditStore (sub-issue D).
- Per-project rule layering / semgrep (the taxonomy is ruff-only; semgrep stays with the canonicalizer).
- Broadening individual rules beyond their ruff defaults.

## Change log

- 2026-05-29: Initial draft (Brent Hoover)
- 2026-05-30: Approved; status draft → active (Brent Hoover)
