---
title: jig init process — Implementation Plan
type: plan
status: superseded
superseded_by: ../../architecture/plan.md
owner: brent
created: 2026-04-24
updated: 2026-06-26
design: ./design.md
---

# jig init process — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace today's template-only `jig init` with a multi-phase,
ticket-backed workflow that produces `project.md`, `project.structured.yaml`,
and `architecture.yaml` before scaffold.

**Architecture:** Reuse existing jig primitives (TicketStore, ThreadStore,
MessageBus, MCP tools, agent spawn via claude-agent-sdk). Extend the data
model (two new `WorkType` values, new `SystemEvent.event_type` literals,
`payload` on `Note`). Introduce three new roles (PO, SA, spec-generator),
a field-based MCP tool surface per role, a one-shot spec-generator agent,
an atomic-write helper, a template metadata registry, and a CLI workflow
coordinator that drives the user-facing prompts. Resume is pure inspection
of persisted ticket state — no new state machine.

**Tech Stack:** Python 3.12, asyncio, pydantic v2, click, pyyaml,
claude-agent-sdk, pytest + pytest-asyncio, ruff, uv.

---

## Overview

Build order follows dependency: data-model extensions first, then
filesystem helpers, then role YAMLs and MCP tool handlers (brief →
spec-gen → SA), then the agent spawn wrapper for spec-gen, then the CLI
conversation loops (entry/stub → PO loop → gap prompt → branch prompt →
SA loop → scaffold), then resume and `--force`, then end-to-end
integration tests. Each task closes with a commit; no task depends on
an uncommitted change from a later task.

## Preconditions

- [ ] All six specs in `feature-work/jig-init-process/specs/` reviewed and approved.
- [ ] `uv sync` succeeds in project root.
- [ ] `uv run pytest tests/ -v` is green on main before starting.
- [ ] Git worktree for this work created (per
      superpowers:using-git-worktrees).

## File Structure

### New files

| Path | Purpose |
|------|---------|
| `jig/atomic.py` | Atomic file-write helper (temp + rename). |
| `jig/markdown_sections.py` | Read/write level-2 sections of `project.md`. |
| `jig/template_registry.py` | Load `template.yaml` metadata from a template dir. |
| `jig/init_mcp.py` | MCP tool handlers for brief / spec-gen / SA. Analog of `ticket_mcp.py`. |
| `jig/spec_generator.py` | `Gap` model, spec-generator agent spawn, validation wiring. |
| `jig/init_workflow.py` | Top-level CLI coordinator: dispatch, prompts, conversation loops. |
| `jig/defaults/roles/po.yaml` | PO agent config. |
| `jig/defaults/roles/sa.yaml` | SA agent config. |
| `jig/defaults/roles/spec-generator.yaml` | Spec-generator agent config. |
| `jig/defaults/project_templates/python/template.yaml` | Metadata for the python template. |
| `jig/defaults/project_templates/fastapi/template.yaml` | Metadata for the fastapi template. |
| `tests/test_init_data_model.py` | WorkType, SystemEvent.event_type, Note.payload, Gap. |
| `tests/test_atomic.py` | Atomic write behavior. |
| `tests/test_markdown_sections.py` | Section parse/update behavior. |
| `tests/test_template_registry.py` | Template metadata loader. |
| `tests/test_init_mcp_brief.py` | brief_* handlers. |
| `tests/test_init_mcp_spec.py` | spec_publish / spec_report_gaps handlers. |
| `tests/test_init_mcp_arch.py` | spec_get_field / arch_* / sa_propose_scaffold. |
| `tests/test_spec_generator.py` | Spec-generator agent spawn (one-shot). |
| `tests/test_init_workflow_resume.py` | Resume state classifier. |
| `tests/test_init_workflow_e2e.py` | End-to-end happy / direct / resume paths. |

### Modified files

| Path | Change |
|------|--------|
| `jig/ticket.py` | Add `WorkType.BRIEF`, `WorkType.ARCHITECTURE`. |
| `jig/thread.py` | Extend `SystemEvent.event_type` literal; add `payload` to `Note`. |
| `jig/mcp_server.py` | Register brief/spec/arch tool groups with role-scoped visibility. |
| `jig/cli.py` | Replace `init` command signature; route to `init_workflow`. |
| `jig/orchestrator.py` | (Minimal) ensure BRIEF/ARCHITECTURE tickets are dispatchable. |

---

## Tasks

### Task 1: Extend data model

**Files:**
- Modify: `jig/ticket.py:10-24`
- Modify: `jig/thread.py:238-252` and `jig/thread.py:383-434`
- Create: `jig/spec_generator.py` (Gap model only in this task)
- Test: `tests/test_init_data_model.py`

**References:** REQ-INIT-BRIEF.1, REQ-INIT-SA.1, REQ-INIT-SPECGEN.4/5,
all `*_SystemEvent` emissions.

- [ ] **Step 1: Write failing tests for the data model extensions**

Create `tests/test_init_data_model.py`:

```python
"""Data model extensions for the init workflow."""
from jig.thread import Note, SystemEvent
from jig.ticket import WorkType
from jig.spec_generator import Gap


def test_worktype_has_brief_and_architecture():
    assert WorkType("brief") == WorkType.BRIEF
    assert WorkType("architecture") == WorkType.ARCHITECTURE


def test_system_event_accepts_new_event_types():
    for et in (
        "spec_generated",
        "spec_gaps_reported",
        "sa_skipped",
        "scaffold_applied",
    ):
        ev = SystemEvent(
            ticket_id="t1",
            author="cli",
            event_type=et,  # type: ignore[arg-type]
            content="",
        )
        assert ev.event_type == et


def test_note_carries_payload():
    n = Note(
        ticket_id="brief",
        author="spec-generator",
        text="gaps",
        payload={"gaps": [{"kind": "missing", "severity": "blocking"}]},
    )
    assert n.payload["gaps"][0]["kind"] == "missing"


def test_note_payload_defaults_empty():
    n = Note(ticket_id="t", author="u", text="hi")
    assert n.payload == {}


def test_gap_model_round_trip():
    g = Gap(
        kind="contradiction",
        location="Non-goals",
        description="Stated non-goal contradicts planned capability X.",
        severity="blocking",
    )
    assert g.suggested_question is None
    dumped = g.model_dump()
    assert dumped["kind"] == "contradiction"
    Gap.model_validate(dumped)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_init_data_model.py -v`
Expected: FAIL — `WorkType.BRIEF` missing, new event_types rejected,
`payload` missing on Note, `jig.spec_generator` module missing.

- [ ] **Step 3: Extend WorkType**

Edit `jig/ticket.py` — inside the `WorkType` enum, add two members
after `DOCS`:

```python
class WorkType(str, Enum):
    """Classification axis: what kind of work this ticket represents.

    Per docs/03-specs-and-work-types.md. The shipped set is intentionally
    small and opinionated.
    """

    FEATURE = "feature"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    SPIKE = "spike"
    PERF = "perf"
    MIGRATION = "migration"
    DOCS = "docs"
    BRIEF = "brief"
    ARCHITECTURE = "architecture"
```

- [ ] **Step 4: Extend SystemEvent.event_type literal**

Edit `jig/thread.py`, locate the `SystemEvent` class and replace its
`event_type` field declaration with:

```python
    event_type: Literal[
        "commit",
        "phase_run",
        "status_change",
        "check_failure",
        "dep_merge_failed",
        "phase_start",
        "phase_end",
        "agent_run",
        "spec_generated",
        "spec_gaps_reported",
        "sa_skipped",
        "scaffold_applied",
    ]
```

- [ ] **Step 5: Add `payload` to Note**

Edit `jig/thread.py`, replace the `Note` class body with:

```python
class Note(_ThreadEntryBase):
    """Freeform observation. Auto-resolved. Replaces the legacy
    ``comment`` kind (Task B migration).

    ``responds_to`` links a Note back at another thread entry it's
    commenting on. Phase 5 Task L uses this for deadlock-nudge
    idempotency — the orchestrator posts at most one nudge per
    blocking entry and keys uniqueness off ``responds_to``. Plain
    human-authored notes leave it ``None``.

    ``payload`` carries structured content for Notes that represent
    machine-generated findings (e.g. spec-generator Gap lists).
    Defaults to an empty dict for plain notes.
    """

    kind: Literal["note"] = "note"
    text: str
    responds_to: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
```

- [ ] **Step 6: Create jig/spec_generator.py with Gap model**

Create `jig/spec_generator.py`:

```python
"""Spec-generator agent scaffolding.

The spec-generator is a one-shot, non-conversational agent that
translates the brief into the structured spec and validates it. This
module defines the Gap payload model; the spawn wrapper is added in a
later task.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Gap(BaseModel):
    """A brief-validation finding reported by the spec-generator."""

    kind: Literal["missing", "contradiction", "ambiguity", "under_specified"]
    location: str
    description: str
    suggested_question: str | None = None
    severity: Literal["blocking", "advisory"]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_init_data_model.py -v`
Expected: PASS (5 tests).

- [ ] **Step 8: Run the full suite to catch regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS — no existing tests broken by the enum/literal/field
extensions.

- [ ] **Step 9: Commit**

```bash
git add jig/ticket.py jig/thread.py jig/spec_generator.py tests/test_init_data_model.py
git commit -m "feat(init): extend data model for init workflow

- Add WorkType.BRIEF and WorkType.ARCHITECTURE
- Add spec_generated, spec_gaps_reported, sa_skipped,
  scaffold_applied to SystemEvent.event_type
- Add payload field to Note for structured Gap content
- Add Gap pydantic model in jig/spec_generator.py"
```

---

### Task 2: Atomic write and markdown section helpers

**Files:**
- Create: `jig/atomic.py`
- Create: `jig/markdown_sections.py`
- Test: `tests/test_atomic.py`
- Test: `tests/test_markdown_sections.py`

**References:** REQ-INIT-BRIEF.8, REQ-INIT-SA.5, REQ-INIT-SCAFFOLD.2/3.

- [ ] **Step 1: Write failing tests for atomic write**

Create `tests/test_atomic.py`:

```python
from pathlib import Path

import pytest

from jig.atomic import atomic_write_text


def test_atomic_write_creates_file(tmp_path: Path):
    target = tmp_path / "data.txt"
    atomic_write_text(target, "hello\n")
    assert target.read_text() == "hello\n"


def test_atomic_write_replaces_existing(tmp_path: Path):
    target = tmp_path / "data.txt"
    target.write_text("old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"


def test_atomic_write_creates_parent(tmp_path: Path):
    target = tmp_path / "nested" / "dir" / "data.txt"
    atomic_write_text(target, "x")
    assert target.read_text() == "x"


def test_atomic_write_no_partial_on_interrupt(tmp_path: Path, monkeypatch):
    """If os.replace raises, the original file must be untouched."""
    import os

    target = tmp_path / "data.txt"
    target.write_text("original")

    orig_replace = os.replace

    def bad_replace(src, dst):
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", bad_replace)
    with pytest.raises(OSError):
        atomic_write_text(target, "corrupted")
    monkeypatch.setattr(os, "replace", orig_replace)
    assert target.read_text() == "original"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_atomic.py -v`
Expected: FAIL — `jig.atomic` module missing.

- [ ] **Step 3: Implement atomic_write_text**

Create `jig/atomic.py`:

```python
"""Atomic file writes via temp + rename.

The write goes to a temp file in the same directory, fsync'd, then
os.replace'd onto the target. If any step before os.replace fails, the
target is untouched. If os.replace itself fails, the temp file is
cleaned up on a best-effort basis and the target is untouched.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write `content` to `path` atomically.

    Creates parent directories if needed. Overwrites existing file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
```

- [ ] **Step 4: Run atomic tests to verify they pass**

Run: `uv run pytest tests/test_atomic.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Write failing tests for markdown_sections**

Create `tests/test_markdown_sections.py`:

```python
from pathlib import Path

import pytest

from jig.markdown_sections import (
    get_section,
    list_sections,
    set_section,
)


SAMPLE = """# todoapp

Task management for individuals.

## Built

- **CRUD for todos** — done

## Planned (committed)

### Due dates
Users should be able to give todos due dates.

## Non-goals

- Sharing
"""


def test_list_sections_returns_level_two_headings(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    assert list_sections(p) == ["Built", "Planned (committed)", "Non-goals"]


def test_get_section_returns_body_without_heading(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    built = get_section(p, "Built")
    assert built.strip() == "- **CRUD for todos** — done"


def test_get_section_missing_raises(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    with pytest.raises(KeyError):
        get_section(p, "Backlog")


def test_set_section_replaces_existing(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    set_section(p, "Non-goals", "- Sharing\n- Calendar\n")
    text = p.read_text()
    assert "- Calendar" in text
    assert text.count("## Non-goals") == 1


def test_set_section_appends_when_missing(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    set_section(p, "Backlog", "- Mobile app\n")
    text = p.read_text()
    assert "## Backlog" in text
    assert "- Mobile app" in text


def test_set_section_on_empty_file_creates_structure(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text("# myproj\n")
    set_section(p, "Built", "- first capability\n")
    text = p.read_text()
    assert "# myproj" in text
    assert "## Built" in text
    assert "- first capability" in text
```

- [ ] **Step 6: Run markdown_sections tests to verify they fail**

Run: `uv run pytest tests/test_markdown_sections.py -v`
Expected: FAIL — module missing.

- [ ] **Step 7: Implement markdown_sections**

Create `jig/markdown_sections.py`:

```python
"""Read/write level-2 (``## Name``) sections of a markdown file.

Used by brief_* MCP tools. Uses atomic_write_text for persistence so
partial writes never corrupt project.md.
"""
from __future__ import annotations

import re
from pathlib import Path

from jig.atomic import atomic_write_text

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def list_sections(path: Path) -> list[str]:
    """Return the names of all level-2 sections, in document order."""
    text = path.read_text()
    return [m.group(1) for m in _HEADING_RE.finditer(text)]


def get_section(path: Path, name: str) -> str:
    """Return the body of the named section (text between its heading
    and the next level-2 heading or EOF), without the heading line.
    Raises KeyError if the section is not present.
    """
    text = path.read_text()
    bounds = _section_bounds(text, name)
    if bounds is None:
        raise KeyError(name)
    _, body_start, body_end = bounds
    return text[body_start:body_end]


def set_section(path: Path, name: str, body: str) -> None:
    """Atomically replace the named section's body, or append a new
    section at EOF if not present. ``body`` should not include the
    heading line. A trailing newline is ensured.
    """
    text = path.read_text() if path.exists() else ""
    if not body.endswith("\n"):
        body = body + "\n"

    bounds = _section_bounds(text, name)
    if bounds is None:
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = f"{text}\n## {name}\n\n{body}"
    else:
        heading_start, body_start, body_end = bounds
        new_text = text[:body_start] + body + text[body_end:]

    atomic_write_text(path, new_text)


def _section_bounds(text: str, name: str) -> tuple[int, int, int] | None:
    """Return (heading_start, body_start, body_end) if ``name`` is a
    level-2 section in ``text``. The body starts after the newline
    following the heading and ends at the next level-2 heading or EOF.
    """
    matches = list(_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        if m.group(1) == name:
            heading_start = m.start()
            # Body starts after the newline that ends the heading line.
            line_end = text.find("\n", m.end())
            body_start = line_end + 1 if line_end != -1 else len(text)
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            return heading_start, body_start, body_end
    return None
```

- [ ] **Step 8: Run markdown_sections tests to verify they pass**

Run: `uv run pytest tests/test_markdown_sections.py -v`
Expected: PASS (6 tests).

- [ ] **Step 9: Commit**

```bash
git add jig/atomic.py jig/markdown_sections.py tests/test_atomic.py tests/test_markdown_sections.py
git commit -m "feat(init): atomic write and markdown section helpers"
```

---

### Task 3: Role YAMLs for PO, SA, spec-generator

**Files:**
- Create: `jig/defaults/roles/po.yaml`
- Create: `jig/defaults/roles/sa.yaml`
- Create: `jig/defaults/roles/spec-generator.yaml`
- Test: `tests/test_init_data_model.py` (extend)

**References:** REQ-INIT-BRIEF.3/4, REQ-INIT-SPECGEN.9, REQ-INIT-SA.4/6.

- [ ] **Step 1: Add failing test that loads the new roles**

Append to `tests/test_init_data_model.py`:

```python
from pathlib import Path

from jig.persistence import load_role


def test_po_role_loads():
    cfg = load_role(Path("/nonexistent-project"), "po")
    assert cfg.role == "po"
    assert "Product Owner" in cfg.phase_prompt or "PO" in cfg.phase_prompt
    assert "brief_set_section" in cfg.allowed_tools
    assert "po_finish_brief" in cfg.allowed_tools


def test_sa_role_loads():
    cfg = load_role(Path("/nonexistent-project"), "sa")
    assert cfg.role == "sa"
    assert "arch_set_field" in cfg.allowed_tools
    assert "sa_propose_scaffold" in cfg.allowed_tools
    # SA must NOT have brief_* tools per REQ-INIT-SA.4
    assert not any(t.startswith("brief_") for t in cfg.allowed_tools)


def test_spec_generator_role_loads():
    cfg = load_role(Path("/nonexistent-project"), "spec-generator")
    assert cfg.role == "spec-generator"
    assert "spec_publish" in cfg.allowed_tools
    assert "spec_report_gaps" in cfg.allowed_tools
    # No conversational tools per REQ-INIT-SPECGEN.9
    assert "ask_question" not in cfg.allowed_tools
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_data_model.py -v -k role`
Expected: FAIL — role YAMLs missing.

- [ ] **Step 3: Write po.yaml**

Create `jig/defaults/roles/po.yaml`:

```yaml
role: po
phase_prompt: >
  You are the Product Owner (PO) agent for jig init.

  Your job is to collaborate with the user to author a project brief
  at docs/brief.md. The brief describes the product shape —
  what it is, who it's for, what's planned, what's explicitly out of
  scope. You are the ONLY agent (besides the spec-generator) that
  reads project.md. Every other agent in jig works off the generated
  structured spec.

  ## Brief format

  Follow the human-authoring format defined in
  docs/reference/02-project-spec.md:

  - An intro paragraph describing the product (what it is, for whom).
  - `## Built` — capabilities already shipped (usually empty at init).
  - `## Planned (committed)` — near-term capabilities elaborated
    under `### <capability-name>` level-3 headings with prose.
  - `## Planned (not yet committed)` — longer-range items as bullets.
  - `## Backlog` — ideas, bullets only.
  - `## Non-goals` — explicit exclusions, bullets with brief rationale.

  ## Process

  1. Before every action, call `brief_list_sections` and read the
     sections you need via `brief_get_section`. The user may edit
     project.md directly at any time; always re-read before acting.

  2. Ask the user clarifying questions via `ask_question`. One topic
     at a time. Focus on product concerns.

  3. Write sections via `brief_set_section` as content converges.
     Each call replaces one `## Name` section atomically.

  4. When the brief is complete enough to validate, call
     `po_finish_brief(summary)` with a one-paragraph summary. This
     hands off to the spec-generator.

  ## Out of scope for you

  Language, framework, deploy target, library choices, architectural
  deliberation. If the user asks about these, tell them the SA path
  handles architecture after the brief is accepted.
allowed_tools:
  - Read
  - ask_question
  - brief_get_section
  - brief_list_sections
  - brief_set_section
  - po_finish_brief
allowed_mcps: []
default_context:
  - "ticket://description"
```

- [ ] **Step 4: Write sa.yaml**

Create `jig/defaults/roles/sa.yaml`:

```yaml
role: sa
phase_prompt: >
  You are the Systems Architect (SA) agent for jig init.

  You have access to the structured project spec at
  .jig/spec/project.structured.yaml. You do NOT have access to
  project.md — the brief is private to the PO.

  Your job is to choose an appropriate scaffold template for this
  project and write architecture decisions to
  .jig/spec/architecture.yaml.

  ## Question style — IMPORTANT

  If you need more information from the user, ask project-shape or
  scoping questions ONLY. Never ask tech-preference questions.

  Good questions:
    - "Does this need a web interface, or is it backend-only?"
    - "Will people interact with this in real time, or is it batch?"
    - "Roughly how many concurrent users or requests per second?"
    - "Does it need to store data? Roughly how much, how structured?"
    - "Any compliance constraints (data residency, audit trail)?"

  Bad questions (never ask these — they belong to the direct path):
    - "Python or TypeScript?"
    - "FastAPI or Django?"
    - "What deploy target do you want?"

  If the user volunteers tech preferences (e.g. "I want FastAPI"),
  accept them and move on. Do not solicit more tech preferences.

  ## Process

  1. Read the structured spec via `spec_get_field` and
     `spec_list_fields` to understand what's being built.

  2. Ask 0–2 scoping questions via `ask_question` if and only if you
     need information the spec doesn't provide.

  3. Write architecture decisions via `arch_set_field`. At minimum,
     set `rationale` (multi-line prose explaining your choice).
     You may also set `data_stores`, `external_services`,
     `deferred_decisions`, or other fields per the starter schema.

  4. Call `sa_propose_scaffold(template_name, rationale, config)`
     with your chosen template. The CLI will show the rationale to
     the user for confirmation.
allowed_tools:
  - Read
  - ask_question
  - spec_get_field
  - spec_list_fields
  - arch_get_field
  - arch_set_field
  - arch_list_fields
  - sa_propose_scaffold
allowed_mcps: []
default_context:
  - "ticket://description"
```

- [ ] **Step 5: Write spec-generator.yaml**

Create `jig/defaults/roles/spec-generator.yaml`:

```yaml
role: spec-generator
phase_prompt: >
  You are the spec-generator agent. You are one-shot and non-
  conversational: you read the project brief and produce a structured
  YAML projection, or report gaps that block publication.

  ## Inputs

  - `docs/brief.md` — the brief. Read it via Read tool.
  - The structured spec schema documented in
    docs/reference/02-project-spec.md.

  ## Required output

  You MUST terminate by calling exactly one of:

  - `spec_publish(yaml, advisory_notes)` — the happy path. `yaml`
    must be a complete, schema-valid YAML string. `advisory_notes`
    is an optional list of non-blocking observations.

  - `spec_report_gaps(gaps)` — the failure path. `gaps` is a list of
    Gap dicts, each with keys `kind`, `location`, `description`,
    optional `suggested_question`, and `severity`.

  ## Validation

  Before calling `spec_publish`, check:

  1. Schema: required fields present, types correct, YAML parses.
  2. Semantic: no contradictions between sections. A non-goal should
     not contradict a planned capability. An elaborated capability
     should not leave its central behavior ambiguous.

  If you find ANY gap with severity "blocking", call
  `spec_report_gaps` instead of `spec_publish`.

  You have no conversational tools. Do not ask the user questions.
  If the brief is too thin to project, report gaps and exit.
allowed_tools:
  - Read
  - spec_publish
  - spec_report_gaps
allowed_mcps: []
default_context:
  - "ticket://description"
```

- [ ] **Step 6: Run tests to verify all three roles load**

Run: `uv run pytest tests/test_init_data_model.py -v -k role`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add jig/defaults/roles/po.yaml jig/defaults/roles/sa.yaml jig/defaults/roles/spec-generator.yaml tests/test_init_data_model.py
git commit -m "feat(init): add PO, SA, spec-generator role configs"
```

---

### Task 4: Template metadata registry

**Files:**
- Create: `jig/template_registry.py`
- Create: `jig/defaults/project_templates/python/template.yaml`
- Create: `jig/defaults/project_templates/fastapi/template.yaml`
- Test: `tests/test_template_registry.py`

**References:** REQ-INIT-SCAFFOLD.7, REQ-INIT-SCAFFOLD.4/5.

- [ ] **Step 1: Write failing tests**

Create `tests/test_template_registry.py`:

```python
from pathlib import Path

import pytest

from jig.template_registry import (
    TemplateMetadata,
    list_templates,
    load_template_metadata,
)


def test_list_templates_returns_shipped_set():
    names = list_templates()
    assert "python" in names
    assert "fastapi" in names


def test_python_template_metadata():
    md = load_template_metadata("python")
    assert isinstance(md, TemplateMetadata)
    assert md.name == "python"
    assert md.language == "python"


def test_fastapi_template_metadata():
    md = load_template_metadata("fastapi")
    assert md.language == "python"
    assert md.framework == "fastapi"


def test_load_unknown_template_raises():
    with pytest.raises(KeyError):
        load_template_metadata("does-not-exist")


def test_template_without_metadata_file_raises(tmp_path: Path, monkeypatch):
    fake_root = tmp_path / "templates"
    fake_root.mkdir()
    (fake_root / "bare").mkdir()
    monkeypatch.setattr(
        "jig.template_registry._templates_root", lambda: fake_root
    )
    with pytest.raises(ValueError, match="missing template.yaml"):
        load_template_metadata("bare")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_template_registry.py -v`
Expected: FAIL — module and yaml files missing.

- [ ] **Step 3: Implement registry**

Create `jig/template_registry.py`:

```python
"""Template metadata registry.

Each template directory under ``jig/defaults/project_templates/``
carries a ``template.yaml`` describing its language, framework, and
deploy target. This metadata populates ``architecture.yaml`` on both
the SA and direct paths.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

_TEMPLATES_DIR = Path(__file__).resolve().parent / "defaults" / "project_templates"


class TemplateMetadata(BaseModel):
    name: str
    description: str = ""
    language: str
    framework: str | None = None
    deploy_target: str | None = None
    package_manager: str = ""


def _templates_root() -> Path:
    return _TEMPLATES_DIR


def list_templates() -> list[str]:
    """Return the sorted names of installed templates."""
    root = _templates_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir())


def load_template_metadata(name: str) -> TemplateMetadata:
    """Load the metadata for a template. Raises KeyError if the
    template does not exist; ValueError if it has no template.yaml.
    """
    root = _templates_root()
    tpl_dir = root / name
    if not tpl_dir.is_dir():
        raise KeyError(f"unknown template: {name!r}")
    meta_path = tpl_dir / "template.yaml"
    if not meta_path.is_file():
        raise ValueError(
            f"template {name!r} missing template.yaml at {meta_path}"
        )
    data = yaml.safe_load(meta_path.read_text()) or {}
    data.setdefault("name", name)
    return TemplateMetadata.model_validate(data)
```

- [ ] **Step 4: Write python template.yaml**

Create `jig/defaults/project_templates/python/template.yaml`:

```yaml
name: python
description: "Python project with uv and pytest."
language: python
framework: null
deploy_target: null
package_manager: uv
```

- [ ] **Step 5: Write fastapi template.yaml**

Create `jig/defaults/project_templates/fastapi/template.yaml`:

```yaml
name: fastapi
description: "FastAPI service with uv, pytest, and a containerized deploy target."
language: python
framework: fastapi
deploy_target: container
package_manager: uv
```

- [ ] **Step 6: Run tests to verify pass**

Run: `uv run pytest tests/test_template_registry.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add jig/template_registry.py jig/defaults/project_templates/python/template.yaml jig/defaults/project_templates/fastapi/template.yaml tests/test_template_registry.py
git commit -m "feat(init): template metadata registry with python and fastapi entries"
```

---

### Task 5: Reserved-id ticket creation helper

**Files:**
- Modify: `jig/store/tickets.py`
- Test: `tests/test_init_data_model.py` (extend)

**References:** REQ-INIT-BRIEF.2, REQ-INIT-SA.2, REQ-INIT-RESUME.12.

- [ ] **Step 1: Add failing test**

Append to `tests/test_init_data_model.py`:

```python
import pytest

from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


@pytest.mark.asyncio
async def test_create_reserved_ticket_uses_given_id(tmp_path):
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    ticket = Ticket(
        id="brief",
        work_type=WorkType.BRIEF,
        title="Project brief",
        created_by="cli",
    )
    tid = await store.create(ticket)
    assert tid == "brief"
    loaded = await store.get("brief")
    assert loaded is not None
    assert loaded.work_type == WorkType.BRIEF


@pytest.mark.asyncio
async def test_create_reserved_ticket_twice_raises(tmp_path):
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    first = Ticket(
        id="brief",
        work_type=WorkType.BRIEF,
        title="t1",
        created_by="cli",
    )
    await store.create(first)
    second = Ticket(
        id="brief",
        work_type=WorkType.BRIEF,
        title="t2",
        created_by="cli",
    )
    with pytest.raises(ValueError, match="already exists"):
        await store.create(second)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_data_model.py -v -k reserved`
Expected: FAIL — current `TicketStore.create` does not enforce id
uniqueness beyond the uuid path; the duplicate test may or may not
fail depending on Collection semantics. Confirm behavior, then
implement both tests.

- [ ] **Step 3: Update TicketStore.create to validate duplicates**

Edit `jig/store/tickets.py`, modify the `create` method to refuse
duplicate ids:

```python
    async def create(self, ticket: Ticket) -> str:
        existing = await self._collection.get(ticket.id)
        if existing is not None:
            raise ValueError(
                f"ticket with id {ticket.id!r} already exists"
            )
        return await self._collection.insert(ticket)
```

(If `self._collection.get` returns `Ticket` here instead of `dict`,
the check still works because `None` is what we're comparing to.)

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_data_model.py -v -k reserved`
Expected: PASS (2 tests).

- [ ] **Step 5: Run full suite to catch regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS — duplicate-id enforcement shouldn't break anything
since UUID collisions don't happen organically.

- [ ] **Step 6: Commit**

```bash
git add jig/store/tickets.py tests/test_init_data_model.py
git commit -m "feat(init): enforce unique ticket ids for reserved id usage"
```

---

### Task 6: brief_* MCP tool handlers

**Files:**
- Create: `jig/init_mcp.py`
- Test: `tests/test_init_mcp_brief.py`

**References:** REQ-INIT-BRIEF.7/8/9/12.

- [ ] **Step 1: Write failing tests**

Create `tests/test_init_mcp_brief.py`:

```python
"""brief_* MCP tool handlers."""
from pathlib import Path

import pytest

from jig.init_mcp import (
    handle_brief_get_section,
    handle_brief_list_sections,
    handle_brief_set_section,
    handle_po_finish_brief,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


@pytest.fixture
async def wired(tmp_path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    brief_path = spec_dir / "project.md"
    brief_path.write_text(
        "# myproj\n\n"
        "Intro paragraph.\n\n"
        "## Built\n\n- A capability\n\n"
        "## Non-goals\n\n- Time tracking\n"
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    brief = Ticket(
        id="brief",
        work_type=WorkType.BRIEF,
        title="Brief",
        created_by="cli",
    )
    await tickets.create(brief)
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "brief_path": brief_path,
        "project_path": tmp_path,
    }


@pytest.mark.asyncio
async def test_brief_list_sections_returns_in_order(wired):
    result = await handle_brief_list_sections(
        project_path=wired["project_path"]
    )
    assert result == ["Built", "Non-goals"]


@pytest.mark.asyncio
async def test_brief_get_section_returns_body(wired):
    body = await handle_brief_get_section(
        project_path=wired["project_path"],
        name="Built",
    )
    assert "A capability" in body


@pytest.mark.asyncio
async def test_brief_get_section_missing_raises(wired):
    with pytest.raises(KeyError):
        await handle_brief_get_section(
            project_path=wired["project_path"],
            name="Backlog",
        )


@pytest.mark.asyncio
async def test_brief_set_section_replaces(wired):
    await handle_brief_set_section(
        project_path=wired["project_path"],
        name="Non-goals",
        markdown="- Time tracking\n- Sharing\n",
    )
    text = wired["brief_path"].read_text()
    assert "- Sharing" in text


@pytest.mark.asyncio
async def test_brief_set_section_appends_when_missing(wired):
    await handle_brief_set_section(
        project_path=wired["project_path"],
        name="Backlog",
        markdown="- Mobile\n",
    )
    text = wired["brief_path"].read_text()
    assert "## Backlog" in text
    assert "- Mobile" in text


@pytest.mark.asyncio
async def test_po_finish_brief_emits_handoff(wired):
    await handle_po_finish_brief(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        summary="Brief complete.",
        author="po",
    )
    entries = await wired["threads"].for_ticket("brief")
    handoffs = [e for e in entries if e.kind == "handoff"]
    assert len(handoffs) == 1
    assert handoffs[0].phase == "spec-generator"
    assert handoffs[0].summary == "Brief complete."


@pytest.mark.asyncio
async def test_po_finish_brief_on_empty_brief_raises(wired):
    wired["brief_path"].write_text("# myproj\n")  # no sections
    with pytest.raises(ValueError, match="empty"):
        await handle_po_finish_brief(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            summary="trying",
            author="po",
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_mcp_brief.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement init_mcp handlers**

Create `jig/init_mcp.py`:

```python
"""MCP tool handlers for the init workflow.

Handlers are structured like ``ticket_mcp.py`` — pure-async functions
that take explicit dependencies, do I/O against the stores and the
filesystem, and return plain values. The ``mcp_server`` module wraps
them in ``@tool`` decorators with role-scoped visibility.
"""
from __future__ import annotations

from pathlib import Path

from jig.markdown_sections import get_section, list_sections, set_section
from jig.store.bus import MessageBus, Message, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff


def _brief_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "project.md"


async def handle_brief_list_sections(*, project_path: Path) -> list[str]:
    return list_sections(_brief_path(project_path))


async def handle_brief_get_section(*, project_path: Path, name: str) -> str:
    return get_section(_brief_path(project_path), name)


async def handle_brief_set_section(
    *,
    project_path: Path,
    name: str,
    markdown: str,
) -> None:
    set_section(_brief_path(project_path), name, markdown)


async def handle_po_finish_brief(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    summary: str,
    author: str,
) -> str:
    """Emit a Handoff on the brief ticket, targeting spec-generator."""
    path = _brief_path(project_path)
    if not path.is_file() or not list_sections(path):
        raise ValueError(
            "cannot finish an empty brief — add at least one section"
        )
    brief = await tickets.get("brief")
    if brief is None:
        raise KeyError("brief ticket not found")
    handoff = Handoff(
        ticket_id="brief",
        author=author,
        phase="spec-generator",
        outputs=["docs/brief.md"],
        summary=summary,
    )
    entry_id = await threads.post(handoff)
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "handoff_posted",
                "ticket_id": "brief",
                "phase": "spec-generator",
            },
            topic="orchestrator",
        )
    )
    return entry_id
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_mcp_brief.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_mcp.py tests/test_init_mcp_brief.py
git commit -m "feat(init): brief_* MCP handlers and po_finish_brief"
```

---

### Task 7: spec_* MCP tool handlers (spec-generator side)

**Files:**
- Modify: `jig/init_mcp.py`
- Test: `tests/test_init_mcp_spec.py`

**References:** REQ-INIT-SPECGEN.2/3/4/5/6/11.

- [ ] **Step 1: Write failing tests**

Create `tests/test_init_mcp_spec.py`:

```python
from pathlib import Path

import pytest
import yaml

from jig.init_mcp import handle_spec_publish, handle_spec_report_gaps
from jig.spec_generator import Gap
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note, SystemEvent
from jig.ticket import Ticket, WorkType


@pytest.fixture
async def wired(tmp_path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    await tickets.create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
    }


@pytest.mark.asyncio
async def test_spec_publish_writes_file_and_emits_event(wired):
    yaml_str = "name: myproj\ncapabilities: {}\n"
    await handle_spec_publish(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        yaml_content=yaml_str,
        advisory_notes=[],
        author="spec-generator",
    )
    spec_file = wired["project_path"] / ".jig" / "spec" / "project.structured.yaml"
    assert spec_file.is_file()
    assert yaml.safe_load(spec_file.read_text())["name"] == "myproj"
    entries = await wired["threads"].for_ticket("brief")
    events = [e for e in entries if isinstance(e, SystemEvent)]
    assert any(e.event_type == "spec_generated" for e in events)


@pytest.mark.asyncio
async def test_spec_publish_with_advisory_notes_posts_note(wired):
    await handle_spec_publish(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        yaml_content="name: x\n",
        advisory_notes=["consider clarifying X"],
        author="spec-generator",
    )
    entries = await wired["threads"].for_ticket("brief")
    notes = [e for e in entries if isinstance(e, Note)]
    assert len(notes) == 1
    assert "consider clarifying X" in notes[0].text


@pytest.mark.asyncio
async def test_spec_report_gaps_posts_note_with_payload(wired):
    gaps = [
        Gap(
            kind="missing",
            location="Planned (committed)",
            description="No capabilities listed.",
            severity="blocking",
        ),
    ]
    await handle_spec_report_gaps(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        gaps=gaps,
        author="spec-generator",
    )
    entries = await wired["threads"].for_ticket("brief")
    notes = [e for e in entries if isinstance(e, Note)]
    assert len(notes) == 1
    assert notes[0].payload["gaps"][0]["kind"] == "missing"
    events = [e for e in entries if isinstance(e, SystemEvent)]
    assert any(e.event_type == "spec_gaps_reported" for e in events)
    spec_file = wired["project_path"] / ".jig" / "spec" / "project.structured.yaml"
    assert not spec_file.exists()


@pytest.mark.asyncio
async def test_spec_publish_rejects_unparseable_yaml(wired):
    with pytest.raises(ValueError, match="parse"):
        await handle_spec_publish(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            yaml_content="::: not yaml :::",
            advisory_notes=[],
            author="spec-generator",
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_mcp_spec.py -v`
Expected: FAIL — handlers missing.

- [ ] **Step 3: Add handlers to init_mcp.py**

Append to `jig/init_mcp.py`:

```python
import yaml

from jig.atomic import atomic_write_text
from jig.spec_generator import Gap
from jig.thread import Note, SystemEvent


def _spec_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "project.structured.yaml"


async def handle_spec_publish(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    yaml_content: str,
    advisory_notes: list[str],
    author: str,
) -> None:
    """Write the structured spec and emit spec_generated."""
    try:
        yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        raise ValueError(f"cannot parse spec YAML: {e}") from e
    brief = await tickets.get("brief")
    if brief is None:
        raise KeyError("brief ticket not found")
    atomic_write_text(_spec_path(project_path), yaml_content)
    if advisory_notes:
        await threads.post(
            Note(
                ticket_id="brief",
                author=author,
                text="Advisory notes:\n" + "\n".join(f"- {n}" for n in advisory_notes),
                payload={"advisory_notes": list(advisory_notes)},
            )
        )
    await threads.post(
        SystemEvent(
            ticket_id="brief",
            author=author,
            event_type="spec_generated",
            content="structured spec written",
        )
    )
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "spec_generated", "ticket_id": "brief"},
            topic="orchestrator",
        )
    )


async def handle_spec_report_gaps(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    gaps: list[Gap],
    author: str,
) -> None:
    """Post a structured Note and emit spec_gaps_reported."""
    brief = await tickets.get("brief")
    if brief is None:
        raise KeyError("brief ticket not found")
    rendered = "\n".join(
        f"- [{g.severity}] {g.location}: {g.description}" for g in gaps
    )
    await threads.post(
        Note(
            ticket_id="brief",
            author=author,
            text=f"Gaps:\n{rendered}",
            payload={"gaps": [g.model_dump() for g in gaps]},
        )
    )
    await threads.post(
        SystemEvent(
            ticket_id="brief",
            author=author,
            event_type="spec_gaps_reported",
            content=f"{len(gaps)} gap(s) reported",
        )
    )
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={"kind": "spec_gaps_reported", "ticket_id": "brief"},
            topic="orchestrator",
        )
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_mcp_spec.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_mcp.py tests/test_init_mcp_spec.py
git commit -m "feat(init): spec_publish and spec_report_gaps handlers"
```

---

### Task 8: spec_get_field / arch_* / sa_propose_scaffold handlers

**Files:**
- Modify: `jig/init_mcp.py`
- Test: `tests/test_init_mcp_arch.py`

**References:** REQ-INIT-SA.3/5/8/11/12.

- [ ] **Step 1: Write failing tests**

Create `tests/test_init_mcp_arch.py`:

```python
from pathlib import Path

import pytest
import yaml

from jig.init_mcp import (
    handle_arch_get_field,
    handle_arch_list_fields,
    handle_arch_set_field,
    handle_sa_propose_scaffold,
    handle_spec_get_field,
    handle_spec_list_fields,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


@pytest.fixture
async def wired(tmp_path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.structured.yaml").write_text(
        yaml.safe_dump({"name": "myproj", "capabilities": {"due-dates": {}}})
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    await tickets.create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="Arch",
            created_by="cli",
        )
    )
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
    }


@pytest.mark.asyncio
async def test_spec_get_field_returns_value(wired):
    v = await handle_spec_get_field(
        project_path=wired["project_path"], path="name"
    )
    assert v == "myproj"


@pytest.mark.asyncio
async def test_spec_get_field_nested(wired):
    v = await handle_spec_get_field(
        project_path=wired["project_path"], path="capabilities.due-dates"
    )
    assert v == {}


@pytest.mark.asyncio
async def test_spec_get_field_missing_returns_none(wired):
    v = await handle_spec_get_field(
        project_path=wired["project_path"], path="nope"
    )
    assert v is None


@pytest.mark.asyncio
async def test_spec_list_fields_recursive(wired):
    fields = await handle_spec_list_fields(project_path=wired["project_path"])
    assert "name" in fields
    assert "capabilities" in fields


@pytest.mark.asyncio
async def test_arch_set_field_creates_file(wired):
    await handle_arch_set_field(
        tickets=wired["tickets"],
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="rationale",
        value="because I said so",
        author="sa",
    )
    arch_file = wired["project_path"] / ".jig" / "spec" / "architecture.yaml"
    assert arch_file.is_file()
    data = yaml.safe_load(arch_file.read_text())
    assert data["rationale"] == "because I said so"


@pytest.mark.asyncio
async def test_arch_set_field_nested(wired):
    await handle_arch_set_field(
        tickets=wired["tickets"],
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="data_stores.0",
        value={"type": "postgres", "purpose": "primary"},
        author="sa",
    )
    arch_file = wired["project_path"] / ".jig" / "spec" / "architecture.yaml"
    data = yaml.safe_load(arch_file.read_text())
    assert data["data_stores"][0]["type"] == "postgres"


@pytest.mark.asyncio
async def test_arch_get_field_reads_back(wired):
    await handle_arch_set_field(
        tickets=wired["tickets"],
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="language",
        value="python",
        author="sa",
    )
    v = await handle_arch_get_field(
        project_path=wired["project_path"], path="language"
    )
    assert v == "python"


@pytest.mark.asyncio
async def test_sa_propose_scaffold_records_proposal(wired):
    await handle_arch_set_field(
        tickets=wired["tickets"],
        threads=wired["threads"],
        project_path=wired["project_path"],
        path="rationale",
        value="spec implies async backend",
        author="sa",
    )
    await handle_sa_propose_scaffold(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        template_name="fastapi",
        rationale="spec implies async backend",
        config={},
        author="sa",
    )
    entries = await wired["threads"].for_ticket("architecture")
    notes = [e for e in entries if e.kind == "note"]
    assert any("fastapi" in n.text for n in notes)


@pytest.mark.asyncio
async def test_sa_propose_scaffold_unknown_template_raises(wired):
    with pytest.raises(KeyError):
        await handle_sa_propose_scaffold(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            template_name="does-not-exist",
            rationale="r",
            config={},
            author="sa",
        )


@pytest.mark.asyncio
async def test_sa_propose_scaffold_empty_rationale_raises(wired):
    with pytest.raises(ValueError, match="rationale"):
        await handle_sa_propose_scaffold(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            template_name="fastapi",
            rationale="",
            config={},
            author="sa",
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_mcp_arch.py -v`
Expected: FAIL — handlers missing.

- [ ] **Step 3: Add YAML path helpers + handlers**

Append to `jig/init_mcp.py`:

```python
from typing import Any

from jig.template_registry import list_templates


def _arch_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "architecture.yaml"


def _yaml_get(data: Any, path: str) -> Any:
    """Walk dotted path; return None if any segment missing."""
    parts = path.split(".")
    current = data
    for p in parts:
        if isinstance(current, dict) and p in current:
            current = current[p]
        elif isinstance(current, list):
            try:
                idx = int(p)
            except ValueError:
                return None
            if 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            return None
    return current


def _yaml_set(data: dict, path: str, value: Any) -> dict:
    """Walk dotted path, creating dicts (and extending lists) as needed."""
    parts = path.split(".")
    cursor: Any = data
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        try:
            idx: int | None = int(p)
        except ValueError:
            idx = None
        if last:
            if idx is not None and isinstance(cursor, list):
                while len(cursor) <= idx:
                    cursor.append(None)
                cursor[idx] = value
            else:
                cursor[p] = value
            return data
        # Non-terminal: ensure container exists.
        if idx is not None:
            if not isinstance(cursor, list):
                raise ValueError(
                    f"path {path!r}: segment {p} expects list, got {type(cursor).__name__}"
                )
            while len(cursor) <= idx:
                cursor.append({})
            if cursor[idx] is None:
                cursor[idx] = {}
            cursor = cursor[idx]
        else:
            if p not in cursor or not isinstance(cursor[p], (dict, list)):
                # Determine child type by peeking at next segment.
                next_p = parts[i + 1]
                try:
                    int(next_p)
                    cursor[p] = []
                except ValueError:
                    cursor[p] = {}
            cursor = cursor[p]
    return data


def _flatten_fields(data: Any, prefix: str = "") -> list[str]:
    out: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            label = f"{prefix}.{k}" if prefix else str(k)
            out.append(label)
            out.extend(_flatten_fields(v, label))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            label = f"{prefix}.{i}" if prefix else str(i)
            out.append(label)
            out.extend(_flatten_fields(v, label))
    return out


async def handle_spec_get_field(*, project_path: Path, path: str) -> Any:
    spec_file = project_path / ".jig" / "spec" / "project.structured.yaml"
    if not spec_file.is_file():
        return None
    data = yaml.safe_load(spec_file.read_text()) or {}
    return _yaml_get(data, path)


async def handle_spec_list_fields(*, project_path: Path) -> list[str]:
    spec_file = project_path / ".jig" / "spec" / "project.structured.yaml"
    if not spec_file.is_file():
        return []
    data = yaml.safe_load(spec_file.read_text()) or {}
    return _flatten_fields(data)


async def handle_arch_get_field(*, project_path: Path, path: str) -> Any:
    arch_file = _arch_path(project_path)
    if not arch_file.is_file():
        return None
    data = yaml.safe_load(arch_file.read_text()) or {}
    return _yaml_get(data, path)


async def handle_arch_list_fields(*, project_path: Path) -> list[str]:
    arch_file = _arch_path(project_path)
    if not arch_file.is_file():
        return []
    data = yaml.safe_load(arch_file.read_text()) or {}
    return _flatten_fields(data)


async def handle_arch_set_field(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    project_path: Path,
    path: str,
    value: Any,
    author: str,
) -> None:
    arch = await tickets.get("architecture")
    if arch is None:
        raise KeyError("architecture ticket not found")
    arch_file = _arch_path(project_path)
    data = yaml.safe_load(arch_file.read_text()) if arch_file.is_file() else {}
    data = data or {}
    _yaml_set(data, path, value)
    atomic_write_text(arch_file, yaml.safe_dump(data, sort_keys=False))
    await threads.post(
        Note(
            ticket_id="architecture",
            author=author,
            text=f"arch_set_field {path}",
            payload={"path": path, "value": value},
        )
    )


async def handle_sa_propose_scaffold(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    template_name: str,
    rationale: str,
    config: dict,
    author: str,
) -> None:
    if not rationale.strip():
        raise ValueError("rationale must not be empty")
    if template_name not in list_templates():
        raise KeyError(f"unknown template: {template_name!r}")
    arch = await tickets.get("architecture")
    if arch is None:
        raise KeyError("architecture ticket not found")
    await threads.post(
        Note(
            ticket_id="architecture",
            author=author,
            text=f"Proposed scaffold: {template_name}\n\nRationale:\n{rationale}",
            payload={
                "kind": "sa_propose_scaffold",
                "template_name": template_name,
                "rationale": rationale,
                "config": config,
            },
        )
    )
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "sa_propose_scaffold",
                "ticket_id": "architecture",
                "template_name": template_name,
            },
            topic="orchestrator",
        )
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_mcp_arch.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_mcp.py tests/test_init_mcp_arch.py
git commit -m "feat(init): spec_get_field, arch_*, sa_propose_scaffold handlers"
```

---

### Task 9: Register new MCP tools with role-scoped visibility

**Files:**
- Modify: `jig/mcp_server.py`

**References:** REQ-INIT-BRIEF.3, REQ-INIT-SA.4, REQ-INIT-SPECGEN.9.

- [ ] **Step 1: Add failing test for role-scoped visibility**

Create `tests/test_init_mcp_registration.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from jig.mcp_server import create_agent_mcp_server
from jig.models import RoleConfig
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


@pytest.fixture
async def stores(tmp_path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    return tickets, threads, memory, bus


@pytest.mark.asyncio
async def test_po_server_exposes_brief_tools(tmp_path, stores):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="po",
        allowed_tools=[
            "brief_get_section",
            "brief_list_sections",
            "brief_set_section",
            "po_finish_brief",
        ],
    )
    server = create_agent_mcp_server(
        tickets=tickets, threads=threads, memory=memory, bus=bus,
        agent_role="po", agent_cfg=cfg,
        worktree_path=tmp_path, project_path=tmp_path,
    )
    names = {t.name for t in server.tools}  # type: ignore[attr-defined]
    assert {"brief_get_section", "brief_set_section", "po_finish_brief"} <= names


@pytest.mark.asyncio
async def test_sa_server_exposes_arch_tools_not_brief(tmp_path, stores):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="sa",
        allowed_tools=[
            "spec_get_field",
            "spec_list_fields",
            "arch_get_field",
            "arch_set_field",
            "arch_list_fields",
            "sa_propose_scaffold",
        ],
    )
    server = create_agent_mcp_server(
        tickets=tickets, threads=threads, memory=memory, bus=bus,
        agent_role="sa", agent_cfg=cfg,
        worktree_path=tmp_path, project_path=tmp_path,
    )
    names = {t.name for t in server.tools}  # type: ignore[attr-defined]
    assert {"arch_set_field", "sa_propose_scaffold"} <= names
    assert not any(n.startswith("brief_") for n in names)


@pytest.mark.asyncio
async def test_spec_generator_server_exposes_only_spec_tools(tmp_path, stores):
    tickets, threads, memory, bus = stores
    cfg = RoleConfig(
        role="spec-generator",
        allowed_tools=["spec_publish", "spec_report_gaps"],
    )
    server = create_agent_mcp_server(
        tickets=tickets, threads=threads, memory=memory, bus=bus,
        agent_role="spec-generator", agent_cfg=cfg,
        worktree_path=tmp_path, project_path=tmp_path,
    )
    names = {t.name for t in server.tools}  # type: ignore[attr-defined]
    assert {"spec_publish", "spec_report_gaps"} <= names
    assert "ask_question" not in names
```

(If the MCP server object's tools list attribute has a different name
than `tools`, replace `.tools` with the correct attribute — discover
during step 2.)

- [ ] **Step 2: Run test and inspect MCP server shape**

Run: `uv run pytest tests/test_init_mcp_registration.py -v`
Expected: FAIL. If the failure is an `AttributeError` about `.tools`,
update the test to use the real attribute before proceeding.

- [ ] **Step 3: Register tool groups in mcp_server.py**

Edit `jig/mcp_server.py`. After the existing `@tool` definitions
inside `create_agent_mcp_server`, add three role-scoped blocks. Each
block registers tools only if the role's `allowed_tools` contains
them. Pseudocode:

```python
    # --- brief tools (PO) ---
    if "brief_get_section" in agent_cfg.allowed_tools:
        @tool(
            "brief_get_section",
            "Read a ## section of project.md by name.",
            {"name": str},
        )
        async def brief_get_section(args):
            body = await init_mcp.handle_brief_get_section(
                project_path=project_path, name=args["name"]
            )
            return {"content": [{"type": "text", "text": body}]}

    if "brief_list_sections" in agent_cfg.allowed_tools:
        @tool(
            "brief_list_sections",
            "List the ## section names in project.md.",
            {},
        )
        async def brief_list_sections(args):
            names = await init_mcp.handle_brief_list_sections(
                project_path=project_path
            )
            return {"content": [{"type": "text", "text": "\n".join(names)}]}

    if "brief_set_section" in agent_cfg.allowed_tools:
        @tool(
            "brief_set_section",
            "Atomically replace a ## section of project.md.",
            {"name": str, "markdown": str},
        )
        async def brief_set_section(args):
            await init_mcp.handle_brief_set_section(
                project_path=project_path,
                name=args["name"],
                markdown=args["markdown"],
            )
            return {"content": [{"type": "text", "text": "ok"}]}

    if "po_finish_brief" in agent_cfg.allowed_tools:
        @tool(
            "po_finish_brief",
            "Signal the brief is complete and hand off to the spec-generator.",
            {"summary": str},
        )
        async def po_finish_brief(args):
            await init_mcp.handle_po_finish_brief(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=project_path,
                summary=args["summary"],
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": "handed off"}]}

    # --- spec-generator tools ---
    if "spec_publish" in agent_cfg.allowed_tools:
        @tool(
            "spec_publish",
            "Publish project.structured.yaml. Optional advisory_notes list.",
            {"yaml": str, "advisory_notes": list},
        )
        async def spec_publish(args):
            await init_mcp.handle_spec_publish(
                tickets=tickets, threads=threads, bus=bus,
                project_path=project_path,
                yaml_content=args["yaml"],
                advisory_notes=args.get("advisory_notes", []),
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": "published"}]}

    if "spec_report_gaps" in agent_cfg.allowed_tools:
        @tool(
            "spec_report_gaps",
            "Report Gap findings; blocks spec publish. Each gap is a dict.",
            {"gaps": list},
        )
        async def spec_report_gaps(args):
            from jig.spec_generator import Gap
            gaps = [Gap.model_validate(g) for g in args["gaps"]]
            await init_mcp.handle_spec_report_gaps(
                tickets=tickets, threads=threads, bus=bus,
                project_path=project_path,
                gaps=gaps, author=agent_role,
            )
            return {"content": [{"type": "text", "text": "reported"}]}

    # --- SA tools ---
    if "spec_get_field" in agent_cfg.allowed_tools:
        @tool(
            "spec_get_field",
            "Read a field from project.structured.yaml by dotted path.",
            {"path": str},
        )
        async def spec_get_field(args):
            v = await init_mcp.handle_spec_get_field(
                project_path=project_path, path=args["path"]
            )
            return {"content": [{"type": "text", "text": repr(v)}]}

    if "spec_list_fields" in agent_cfg.allowed_tools:
        @tool(
            "spec_list_fields",
            "List all dotted field paths in project.structured.yaml.",
            {},
        )
        async def spec_list_fields(args):
            fields = await init_mcp.handle_spec_list_fields(
                project_path=project_path
            )
            return {"content": [{"type": "text", "text": "\n".join(fields)}]}

    if "arch_get_field" in agent_cfg.allowed_tools:
        @tool(
            "arch_get_field",
            "Read a field from architecture.yaml by dotted path.",
            {"path": str},
        )
        async def arch_get_field(args):
            v = await init_mcp.handle_arch_get_field(
                project_path=project_path, path=args["path"]
            )
            return {"content": [{"type": "text", "text": repr(v)}]}

    if "arch_set_field" in agent_cfg.allowed_tools:
        @tool(
            "arch_set_field",
            "Atomically write a field in architecture.yaml.",
            {"path": str, "value": dict},
        )
        async def arch_set_field(args):
            await init_mcp.handle_arch_set_field(
                tickets=tickets, threads=threads,
                project_path=project_path,
                path=args["path"], value=args["value"],
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": "set"}]}

    if "arch_list_fields" in agent_cfg.allowed_tools:
        @tool(
            "arch_list_fields",
            "List all dotted field paths in architecture.yaml.",
            {},
        )
        async def arch_list_fields(args):
            fields = await init_mcp.handle_arch_list_fields(
                project_path=project_path
            )
            return {"content": [{"type": "text", "text": "\n".join(fields)}]}

    if "sa_propose_scaffold" in agent_cfg.allowed_tools:
        @tool(
            "sa_propose_scaffold",
            "Propose a scaffold template with rationale.",
            {"template_name": str, "rationale": str, "config": dict},
        )
        async def sa_propose_scaffold(args):
            await init_mcp.handle_sa_propose_scaffold(
                tickets=tickets, threads=threads, bus=bus,
                project_path=project_path,
                template_name=args["template_name"],
                rationale=args["rationale"],
                config=args.get("config", {}),
                author=agent_role,
            )
            return {"content": [{"type": "text", "text": "proposed"}]}
```

Also add `from jig import init_mcp` at the top of the file.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_mcp_registration.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run full suite for regressions**

Run: `uv run pytest tests/ -v`
Expected: PASS across the board.

- [ ] **Step 6: Commit**

```bash
git add jig/mcp_server.py tests/test_init_mcp_registration.py
git commit -m "feat(init): register brief/spec/arch MCP tools with role-scoped visibility"
```

---

### Task 10: Spec-generator agent spawn (one-shot)

**Files:**
- Modify: `jig/spec_generator.py`
- Test: `tests/test_spec_generator.py`

**References:** REQ-INIT-SPECGEN.1/2/9/11.

- [ ] **Step 1: Write failing test**

Create `tests/test_spec_generator.py`:

```python
"""Spec-generator one-shot spawn."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jig.spec_generator import run_spec_generator
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


@pytest.mark.asyncio
async def test_run_spec_generator_invokes_agent(tmp_path: Path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    (tmp_path / ".jig" / "spec" / "project.md").write_text(
        "# p\n\n## Built\n\n- one\n"
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    await tickets.create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )

    with patch("jig.spec_generator.run_agent_for_ticket", new=AsyncMock()) as mock_run:
        await run_spec_generator(
            project_path=tmp_path,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
        )
        assert mock_run.await_count == 1
        call_kwargs = mock_run.await_args.kwargs
        assert call_kwargs["role"] == "spec-generator"
        assert call_kwargs["ticket_id"] == "brief"


@pytest.mark.asyncio
async def test_run_spec_generator_missing_brief_raises(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    with pytest.raises(KeyError, match="brief"):
        await run_spec_generator(
            project_path=tmp_path,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
        )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_spec_generator.py -v`
Expected: FAIL — `run_spec_generator` missing.

- [ ] **Step 3: Implement run_spec_generator**

Append to `jig/spec_generator.py`:

```python
from pathlib import Path

from jig.agent import run_agent_for_ticket
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


async def run_spec_generator(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
) -> None:
    """Spawn the one-shot spec-generator agent on the brief ticket.

    Returns when the agent process exits. The agent is responsible for
    calling spec_publish or spec_report_gaps before exit; if it exits
    without either, resume logic re-runs it on the next init.
    """
    brief = await tickets.get("brief")
    if brief is None:
        raise KeyError("brief ticket not found")
    await run_agent_for_ticket(
        project_path=project_path,
        role="spec-generator",
        ticket_id="brief",
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )
```

(If the actual signature of `run_agent_for_ticket` differs, adapt
the call — the important invariant is that the role is
`"spec-generator"` and the ticket is `"brief"`.)

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_spec_generator.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/spec_generator.py tests/test_spec_generator.py
git commit -m "feat(init): one-shot spec-generator agent spawn"
```

---

### Task 11: CLI init entry — signature, directory state, stub creation

**Files:**
- Modify: `jig/cli.py`
- Create: `jig/init_workflow.py`
- Test: `tests/test_init_workflow.py`

**References:** REQ-INIT-CLI.1/2/3/4/5/7/15.

- [ ] **Step 1: Write failing tests for directory state detection and stub**

Create `tests/test_init_workflow.py`:

```python
"""CLI init entry: directory state, stub creation, top-level dispatch."""
from pathlib import Path

import pytest

from jig.init_workflow import (
    DirState,
    classify_directory,
    create_stub,
)


def test_classify_fresh_parent_missing(tmp_path: Path):
    target = tmp_path / "new-project"
    assert classify_directory(target) == DirState.FRESH


def test_classify_fresh_parent_exists(tmp_path: Path):
    target = tmp_path / "fresh-dir"
    target.mkdir()
    assert classify_directory(target) == DirState.FRESH


def test_classify_already_scaffolded(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    (tmp_path / ".jig" / "project.yaml").write_text(
        "id: x\nname: x\ncreated_at: 2026-01-01T00:00:00Z\n"
        "template_name: python\ntemplate_applied_at: 2026-01-01T00:00:00Z\n"
    )
    assert classify_directory(tmp_path) == DirState.ALREADY_DONE


def test_classify_in_progress_brief_only(tmp_path: Path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    (tmp_path / ".jig" / "project.yaml").write_text(
        "id: x\nname: x\ncreated_at: 2026-01-01T00:00:00Z\n"
    )
    (tmp_path / ".jig" / "spec" / "project.md").write_text("# x\n")
    assert classify_directory(tmp_path) == DirState.IN_PROGRESS


def test_classify_partial_broken(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    # project.yaml missing; this is inconsistent.
    assert classify_directory(tmp_path) == DirState.BROKEN


def test_create_stub(tmp_path: Path):
    target = tmp_path / "new"
    create_stub(target, name="new")
    assert (target / ".jig" / "project.yaml").is_file()
    assert (target / ".jig" / "spec" / "project.md").is_file()
    import yaml
    data = yaml.safe_load((target / ".jig" / "project.yaml").read_text())
    assert data["name"] == "new"
    assert "id" in data
    assert "created_at" in data


def test_create_stub_idempotent_when_consistent(tmp_path: Path):
    target = tmp_path / "new"
    create_stub(target, name="new")
    create_stub(target, name="new")  # should not raise
    # project.yaml not overwritten — check id stays stable.
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_workflow.py -v`
Expected: FAIL — `init_workflow` module missing.

- [ ] **Step 3: Implement classify_directory + create_stub**

Create `jig/init_workflow.py`:

```python
"""CLI coordinator for `jig init <name>`.

Dispatches between fresh-init and resume, drives the PO / spec-gen /
SA conversation loops, and finalizes scaffold.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import yaml

from jig.atomic import atomic_write_text


class DirState(str, Enum):
    FRESH = "fresh"
    IN_PROGRESS = "in_progress"
    ALREADY_DONE = "already_done"
    BROKEN = "broken"


def classify_directory(path: Path) -> DirState:
    """Inspect ``path`` and decide which branch of init to run.

    Pure function — no side effects.
    """
    if not path.exists() or not (path / ".jig").is_dir():
        return DirState.FRESH
    project_yaml = path / ".jig" / "project.yaml"
    if not project_yaml.is_file():
        return DirState.BROKEN
    try:
        data = yaml.safe_load(project_yaml.read_text()) or {}
    except yaml.YAMLError:
        return DirState.BROKEN
    if not {"id", "name", "created_at"} <= data.keys():
        return DirState.BROKEN
    if data.get("template_applied_at"):
        return DirState.ALREADY_DONE
    return DirState.IN_PROGRESS


def create_stub(path: Path, *, name: str) -> None:
    """Create the minimal on-disk stub: .jig/project.yaml and
    docs/brief.md. Idempotent: does not overwrite an existing
    project.yaml.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / ".jig").mkdir(exist_ok=True)
    (path / ".jig" / "spec").mkdir(exist_ok=True)
    project_yaml = path / ".jig" / "project.yaml"
    if not project_yaml.is_file():
        data = {
            "id": str(uuid.uuid4()),
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_write_text(project_yaml, yaml.safe_dump(data, sort_keys=False))
    brief = path / ".jig" / "spec" / "project.md"
    if not brief.is_file():
        atomic_write_text(brief, f"# {name}\n")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_workflow.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Wire the new CLI command**

Replace the existing `init` command in `jig/cli.py` with a new
signature. Keep the old helpers (`_available_templates`,
`_apply_template`) for now — they'll be reused by the scaffold task.

Edit `jig/cli.py`, replace the existing `@cli.command() def init(...)`
block with:

```python
@cli.command()
@click.argument("name")
@click.option("--force", is_flag=True, help="Wipe .jig/ state and restart.")
def init(name: str, force: bool) -> None:
    """Initialize a new jig project by brief → spec → architecture → scaffold."""
    import asyncio

    from jig.init_workflow import run_init

    asyncio.run(run_init(name=name, force=force))
```

- [ ] **Step 6: Stub run_init**

Append to `jig/init_workflow.py`:

```python
import click


async def run_init(*, name: str, force: bool) -> None:
    """Top-level init flow. Fleshed out across subsequent tasks.

    At this task stage, run_init only handles fresh/already-done/broken
    branches and creates the stub. PO spawn, spec-gen, branch prompt,
    SA, and scaffold are added in later tasks.
    """
    target = Path(name)
    state = classify_directory(target)
    if state == DirState.ALREADY_DONE and not force:
        raise click.ClickException(
            f"{target} already initialized. Use --force to restart from scratch."
        )
    if state == DirState.BROKEN and not force:
        raise click.ClickException(
            f"{target}/.jig is in an inconsistent state. Use --force to reset."
        )
    if force and (target / ".jig").is_dir():
        _confirm_force(target)
        import shutil
        shutil.rmtree(target / ".jig")
    create_stub(target, name=name)
    click.echo(f"Initialized stub at {target}/.jig")
    # Later tasks wire the rest of the flow here.


def _confirm_force(target: Path) -> None:
    reply = click.prompt(
        f"This will wipe {target}/.jig. Type 'force' to continue",
        default="",
        show_default=False,
    )
    if reply != "force":
        raise click.ClickException("Aborted.")
```

- [ ] **Step 7: Smoke-test the CLI manually**

Run: `uv run jig init testproj-$(date +%s)`
Expected: creates a directory with `.jig/project.yaml` and
`docs/brief.md`, prints "Initialized stub at ...".

- [ ] **Step 8: Clean up the smoke-test directory**

Run: `rm -rf testproj-*`

- [ ] **Step 9: Full suite**

Run: `uv run pytest tests/ -v`
Expected: PASS — the CLI change only renames the signature; the old
tests that exercised `init_project` directly via `jig/persistence.py`
should still pass if they exist.

- [ ] **Step 10: Commit**

```bash
git add jig/cli.py jig/init_workflow.py tests/test_init_workflow.py
git commit -m "feat(init): CLI entry, directory state classifier, stub creation"
```

---

### Task 12: PO conversation loop

**Files:**
- Modify: `jig/init_workflow.py`
- Test: `tests/test_init_workflow.py` (extend)

**References:** REQ-INIT-CLI.8/9, REQ-INIT-BRIEF.2/3/5/6/9.

- [ ] **Step 1: Write failing test**

Append to `tests/test_init_workflow.py`:

```python
from unittest.mock import AsyncMock, patch

from jig.init_workflow import run_po_conversation
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


@pytest.mark.asyncio
async def test_run_po_conversation_creates_brief_ticket_and_spawns(tmp_path: Path):
    create_stub(tmp_path / "p", name="p")
    project = tmp_path / "p"
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    memory = MemoryStore(project / ".jig" / "store")
    bus = MessageBus(project / ".jig" / "store" / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()

    with patch(
        "jig.init_workflow.run_agent_for_ticket", new=AsyncMock()
    ) as mock_run:
        await run_po_conversation(
            project_path=project,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
        )
        mock_run.assert_awaited_once()

    brief = await tickets.get("brief")
    assert brief is not None
    assert brief.work_type == WorkType.BRIEF


@pytest.mark.asyncio
async def test_run_po_conversation_is_idempotent_on_existing_brief(tmp_path: Path):
    create_stub(tmp_path / "p", name="p")
    project = tmp_path / "p"
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    memory = MemoryStore(project / ".jig" / "store")
    bus = MessageBus(project / ".jig" / "store" / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()
    await tickets.create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )

    with patch(
        "jig.init_workflow.run_agent_for_ticket", new=AsyncMock()
    ):
        await run_po_conversation(
            project_path=project,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
        )

    # Brief ticket is still the same one — create would have raised on
    # duplicate id per Task 5.
    brief = await tickets.get("brief")
    assert brief is not None
    assert brief.id == "brief"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_workflow.py -v -k po_conversation`
Expected: FAIL — function missing.

- [ ] **Step 3: Implement run_po_conversation**

Append to `jig/init_workflow.py`:

```python
from jig.agent import run_agent_for_ticket
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


async def run_po_conversation(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
) -> None:
    """Create (if needed) the brief ticket and run the PO agent on it.

    The agent drives the conversation via its MCP tools and the CLI
    bridge handled inside run_agent_for_ticket. When PO calls
    po_finish_brief, a Handoff is posted on the brief ticket and the
    agent exits cleanly.
    """
    brief = await tickets.get("brief")
    if brief is None:
        brief = Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="Project brief",
            description="",
            created_by="cli",
        )
        await tickets.create(brief)
    await run_agent_for_ticket(
        project_path=project_path,
        role="po",
        ticket_id="brief",
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_workflow.py -v -k po_conversation`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_workflow.py tests/test_init_workflow.py
git commit -m "feat(init): PO conversation bootstrap — brief ticket + agent spawn"
```

---

### Task 13: Gap prompt and Resume-PO / Quit dispatch

**Files:**
- Modify: `jig/init_workflow.py`
- Test: `tests/test_init_workflow.py` (extend)

**References:** REQ-INIT-CLI.10, REQ-INIT-SPECGEN.4, REQ-INIT-RESUME.4.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_init_workflow.py`:

```python
from jig.init_workflow import latest_gap_note, render_gap_prompt
from jig.spec_generator import Gap
from jig.thread import Note


@pytest.mark.asyncio
async def test_latest_gap_note_returns_most_recent(tmp_path: Path):
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    await threads.post(
        Note(
            ticket_id="brief",
            author="spec-generator",
            text="first",
            payload={"gaps": [{"kind": "missing", "severity": "blocking",
                               "location": "x", "description": "d1"}]},
        )
    )
    await threads.post(
        Note(
            ticket_id="brief",
            author="spec-generator",
            text="second",
            payload={"gaps": [{"kind": "ambiguity", "severity": "blocking",
                               "location": "y", "description": "d2"}]},
        )
    )
    note = await latest_gap_note(threads)
    assert note is not None
    assert note.text == "second"


def test_render_gap_prompt_formats_gaps():
    gaps = [
        Gap(kind="missing", location="Built", description="X", severity="blocking"),
        Gap(
            kind="ambiguity",
            location="Planned",
            description="Y",
            severity="blocking",
        ),
    ]
    text = render_gap_prompt(gaps)
    assert "[R] Resume PO" in text
    assert "[Q] Quit" in text
    assert "X" in text
    assert "Y" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_workflow.py -v -k gap`
Expected: FAIL — functions missing.

- [ ] **Step 3: Implement gap helpers**

Append to `jig/init_workflow.py`:

```python
from jig.spec_generator import Gap
from jig.thread import Note


async def latest_gap_note(threads: ThreadStore) -> Note | None:
    """Return the most recent Gap-bearing Note on the brief ticket,
    or None if no gaps have been reported.
    """
    entries = await threads.for_ticket("brief")
    gap_notes = [
        e for e in entries
        if isinstance(e, Note) and "gaps" in e.payload
    ]
    if not gap_notes:
        return None
    return gap_notes[-1]


def render_gap_prompt(gaps: list[Gap]) -> str:
    lines = ["Spec generation found gaps in the brief:"]
    for g in gaps:
        lines.append(f"  - [{g.severity}] {g.location}: {g.description}")
    lines.append("")
    lines.append("[R] Resume PO conversation to address  (default)")
    lines.append("[Q] Quit (state saved; resume later with `jig init <name>`)")
    return "\n".join(lines)


async def prompt_gap_decision(
    threads: ThreadStore,
) -> str:
    """Display the gap prompt and return the user's decision ('R' or 'Q')."""
    note = await latest_gap_note(threads)
    if note is None:
        raise RuntimeError("prompt_gap_decision called with no gap note")
    gaps = [Gap.model_validate(g) for g in note.payload["gaps"]]
    click.echo(render_gap_prompt(gaps))
    reply = click.prompt("Choice", default="R", show_default=False).strip().upper()
    if reply not in ("R", "Q"):
        reply = "R"
    return reply
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_workflow.py -v -k gap`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_workflow.py tests/test_init_workflow.py
git commit -m "feat(init): gap prompt rendering and Resume/Quit dispatch"
```

---

### Task 14: Branch prompt (SA / Direct / Stay)

**Files:**
- Modify: `jig/init_workflow.py`
- Test: `tests/test_init_workflow.py` (extend)

**References:** REQ-INIT-CLI.11.

- [ ] **Step 1: Write failing test**

Append to `tests/test_init_workflow.py`:

```python
from jig.init_workflow import BranchChoice, render_branch_prompt


def test_render_branch_prompt_contains_choices():
    text = render_branch_prompt()
    assert "[Y]" in text and "SA" in text
    assert "[p]" in text and "template" in text.lower()
    assert "[s]" in text and "PO" in text


def test_branch_choice_parsing():
    assert BranchChoice.parse("") == BranchChoice.SA
    assert BranchChoice.parse("Y") == BranchChoice.SA
    assert BranchChoice.parse("y") == BranchChoice.SA
    assert BranchChoice.parse("p") == BranchChoice.DIRECT
    assert BranchChoice.parse("s") == BranchChoice.STAY
    assert BranchChoice.parse("garbage") == BranchChoice.SA
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_workflow.py -v -k branch`
Expected: FAIL — symbols missing.

- [ ] **Step 3: Implement branch prompt**

Append to `jig/init_workflow.py`:

```python
class BranchChoice(str, Enum):
    SA = "sa"
    DIRECT = "direct"
    STAY = "stay"

    @classmethod
    def parse(cls, reply: str) -> "BranchChoice":
        r = reply.strip().lower()
        if r in ("", "y"):
            return cls.SA
        if r == "p":
            return cls.DIRECT
        if r == "s":
            return cls.STAY
        return cls.SA


def render_branch_prompt() -> str:
    return (
        "Brief accepted. Choose your path:\n"
        "  [Y] Hand off to SA for architecture + template  (default)\n"
        "  [p] Pick a template yourself from the list\n"
        "  [s] Stay on PO — brief needs more work\n"
    )


async def prompt_branch_choice() -> BranchChoice:
    click.echo(render_branch_prompt())
    reply = click.prompt("Choice", default="Y", show_default=False)
    return BranchChoice.parse(reply)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_workflow.py -v -k branch`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_workflow.py tests/test_init_workflow.py
git commit -m "feat(init): branch prompt between SA/direct/stay"
```

---

### Task 15: SA conversation loop and confirmation prompt

**Files:**
- Modify: `jig/init_workflow.py`
- Test: `tests/test_init_workflow.py` (extend)

**References:** REQ-INIT-CLI.12, REQ-INIT-SA.2/3/8/9.

- [ ] **Step 1: Write failing test**

Append to `tests/test_init_workflow.py`:

```python
from unittest.mock import AsyncMock, patch

from jig.init_workflow import (
    ConfirmChoice,
    latest_scaffold_proposal,
    render_sa_confirm_prompt,
    run_sa_conversation,
)
from jig.thread import Note


def test_confirm_choice_parsing():
    assert ConfirmChoice.parse("") == ConfirmChoice.YES
    assert ConfirmChoice.parse("Y") == ConfirmChoice.YES
    assert ConfirmChoice.parse("n") == ConfirmChoice.NO
    assert ConfirmChoice.parse("swap") == ConfirmChoice.SWAP


def test_render_sa_confirm_prompt_shows_rationale():
    text = render_sa_confirm_prompt(
        template_name="fastapi",
        rationale="real-time API, async needs.",
    )
    assert "fastapi" in text
    assert "real-time API, async needs." in text
    assert "[Y/n/swap]" in text


@pytest.mark.asyncio
async def test_latest_scaffold_proposal_returns_most_recent(tmp_path: Path):
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    await threads.post(
        Note(
            ticket_id="architecture",
            author="sa",
            text="first",
            payload={
                "kind": "sa_propose_scaffold",
                "template_name": "python",
                "rationale": "simple",
                "config": {},
            },
        )
    )
    await threads.post(
        Note(
            ticket_id="architecture",
            author="sa",
            text="second",
            payload={
                "kind": "sa_propose_scaffold",
                "template_name": "fastapi",
                "rationale": "async",
                "config": {},
            },
        )
    )
    proposal = await latest_scaffold_proposal(threads)
    assert proposal is not None
    assert proposal["template_name"] == "fastapi"


@pytest.mark.asyncio
async def test_run_sa_conversation_creates_arch_ticket_and_spawns(tmp_path: Path):
    create_stub(tmp_path / "p", name="p")
    project = tmp_path / "p"
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    memory = MemoryStore(project / ".jig" / "store")
    bus = MessageBus(project / ".jig" / "store" / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()

    with patch(
        "jig.init_workflow.run_agent_for_ticket", new=AsyncMock()
    ) as mock_run:
        await run_sa_conversation(
            project_path=project,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
        )
        mock_run.assert_awaited_once()

    arch = await tickets.get("architecture")
    assert arch is not None
    assert arch.work_type == WorkType.ARCHITECTURE
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_workflow.py -v -k sa`
Expected: FAIL — symbols missing.

- [ ] **Step 3: Implement SA wrappers**

Append to `jig/init_workflow.py`:

```python
class ConfirmChoice(str, Enum):
    YES = "yes"
    NO = "no"
    SWAP = "swap"

    @classmethod
    def parse(cls, reply: str) -> "ConfirmChoice":
        r = reply.strip().lower()
        if r in ("", "y"):
            return cls.YES
        if r == "n":
            return cls.NO
        if r == "swap":
            return cls.SWAP
        return cls.YES


def render_sa_confirm_prompt(*, template_name: str, rationale: str) -> str:
    return (
        f"SA proposes: {template_name}\n\n"
        f"Rationale:\n{rationale}\n\n"
        "[Y/n/swap]  (Y = accept, n = cancel, swap = re-consult SA)"
    )


async def latest_scaffold_proposal(threads: ThreadStore) -> dict | None:
    """Return the payload dict of the most recent sa_propose_scaffold
    Note on the architecture ticket, or None if none exists.
    """
    entries = await threads.for_ticket("architecture")
    proposals = [
        e for e in entries
        if isinstance(e, Note) and e.payload.get("kind") == "sa_propose_scaffold"
    ]
    if not proposals:
        return None
    return dict(proposals[-1].payload)


async def prompt_sa_confirm(threads: ThreadStore) -> tuple[ConfirmChoice, dict | None]:
    proposal = await latest_scaffold_proposal(threads)
    if proposal is None:
        raise RuntimeError("prompt_sa_confirm called with no proposal")
    click.echo(render_sa_confirm_prompt(
        template_name=proposal["template_name"],
        rationale=proposal["rationale"],
    ))
    reply = click.prompt("Choice", default="Y", show_default=False)
    return ConfirmChoice.parse(reply), proposal


async def run_sa_conversation(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
) -> None:
    arch = await tickets.get("architecture")
    if arch is None:
        arch = Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="Architecture",
            description="",
            created_by="cli",
        )
        await tickets.create(arch)
    await run_agent_for_ticket(
        project_path=project_path,
        role="sa",
        ticket_id="architecture",
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_workflow.py -v -k sa`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_workflow.py tests/test_init_workflow.py
git commit -m "feat(init): SA conversation + confirmation prompt"
```

---

### Task 16: Direct-pick template list UI

**Files:**
- Modify: `jig/init_workflow.py`
- Test: `tests/test_init_workflow.py` (extend)

**References:** REQ-INIT-SCAFFOLD.10, REQ-INIT-SA.10.

- [ ] **Step 1: Write failing test**

Append to `tests/test_init_workflow.py`:

```python
from jig.init_workflow import (
    create_sa_skipped_marker,
    render_template_list,
)
from jig.thread import SystemEvent


def test_render_template_list_shows_numbered_choices():
    text = render_template_list(["python", "fastapi"])
    assert "1)" in text
    assert "python" in text
    assert "2)" in text
    assert "fastapi" in text


@pytest.mark.asyncio
async def test_create_sa_skipped_marker_creates_ticket_and_event(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await tickets.load()
    await threads.load()
    await create_sa_skipped_marker(tickets=tickets, threads=threads)
    arch = await tickets.get("architecture")
    assert arch is not None
    entries = await threads.for_ticket("architecture")
    events = [e for e in entries if isinstance(e, SystemEvent)]
    assert any(e.event_type == "sa_skipped" for e in events)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_workflow.py -v -k "template_list or sa_skipped"`
Expected: FAIL.

- [ ] **Step 3: Implement direct-pick helpers**

Append to `jig/init_workflow.py`:

```python
from jig.template_registry import list_templates, load_template_metadata
from jig.thread import SystemEvent


def render_template_list(names: list[str]) -> str:
    lines = ["Available templates:"]
    for i, n in enumerate(names, start=1):
        try:
            md = load_template_metadata(n)
            desc = md.description
        except Exception:
            desc = ""
        lines.append(f"  {i}) {n} — {desc}" if desc else f"  {i}) {n}")
    return "\n".join(lines)


async def prompt_direct_template() -> str:
    names = list_templates()
    while True:
        click.echo(render_template_list(names))
        reply = click.prompt(
            f"Pick (1-{len(names)})", default="1", show_default=False
        ).strip()
        try:
            idx = int(reply)
        except ValueError:
            click.echo("Please enter a number.")
            continue
        if 1 <= idx <= len(names):
            return names[idx - 1]
        click.echo("Out of range.")


async def create_sa_skipped_marker(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
) -> None:
    arch = await tickets.get("architecture")
    if arch is None:
        arch = Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="Architecture",
            description="",
            created_by="cli",
        )
        await tickets.create(arch)
    await threads.post(
        SystemEvent(
            ticket_id="architecture",
            author="cli",
            event_type="sa_skipped",
            content="user chose direct-pick",
        )
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_workflow.py -v -k "template_list or sa_skipped"`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_workflow.py tests/test_init_workflow.py
git commit -m "feat(init): direct-pick template list and sa_skipped marker"
```

---

### Task 17: Scaffold application (template copy + architecture.yaml finalization)

**Files:**
- Modify: `jig/init_workflow.py`
- Test: `tests/test_init_workflow.py` (extend)

**References:** REQ-INIT-SCAFFOLD.1/2/3/4/5/6/9.

- [ ] **Step 1: Write failing test**

Append to `tests/test_init_workflow.py`:

```python
from unittest.mock import patch

from jig.init_workflow import apply_scaffold


@pytest.mark.asyncio
async def test_apply_scaffold_direct_path_writes_architecture_yaml(tmp_path: Path):
    create_stub(tmp_path / "p", name="p")
    project = tmp_path / "p"
    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    await tickets.load()
    await threads.load()
    await create_sa_skipped_marker(tickets=tickets, threads=threads)

    with patch("jig.init_workflow._apply_template_files"):
        await apply_scaffold(
            project_path=project,
            template_name="python",
            sa_path=False,
            config=None,
            tickets=tickets,
            threads=threads,
        )

    arch_file = project / ".jig" / "spec" / "architecture.yaml"
    assert arch_file.is_file()
    import yaml
    data = yaml.safe_load(arch_file.read_text())
    assert data["template"] == "python"
    assert data["sa_path"] is False
    assert data["language"] == "python"
    assert "rationale" not in data

    project_yaml = yaml.safe_load((project / ".jig" / "project.yaml").read_text())
    assert project_yaml["template_name"] == "python"
    assert "template_applied_at" in project_yaml

    events = await threads.for_ticket("architecture")
    assert any(
        isinstance(e, SystemEvent) and e.event_type == "scaffold_applied"
        for e in events
    )


@pytest.mark.asyncio
async def test_apply_scaffold_sa_path_preserves_sa_fields(tmp_path: Path):
    create_stub(tmp_path / "p", name="p")
    project = tmp_path / "p"
    arch_dir = project / ".jig" / "spec"
    import yaml
    yaml_text = yaml.safe_dump({
        "rationale": "fastapi is a good fit",
        "data_stores": [{"type": "postgres", "purpose": "primary"}],
    })
    (arch_dir / "architecture.yaml").write_text(yaml_text)

    tickets = TicketStore(project / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(project / ".jig" / "store" / "comments.jsonl")
    await tickets.load()
    await threads.load()
    from jig.ticket import Ticket, WorkType
    await tickets.create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="Architecture",
            created_by="cli",
        )
    )

    with patch("jig.init_workflow._apply_template_files"):
        await apply_scaffold(
            project_path=project,
            template_name="fastapi",
            sa_path=True,
            config={"port": 8000},
            tickets=tickets,
            threads=threads,
        )

    data = yaml.safe_load((arch_dir / "architecture.yaml").read_text())
    assert data["rationale"] == "fastapi is a good fit"
    assert data["sa_path"] is True
    assert data["template"] == "fastapi"
    assert data["language"] == "python"
    assert data["framework"] == "fastapi"
    assert data["config"] == {"port": 8000}
    assert data["data_stores"][0]["type"] == "postgres"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_workflow.py -v -k scaffold`
Expected: FAIL — `apply_scaffold` missing.

- [ ] **Step 3: Extract template copy logic**

Move the existing template application logic. In `jig/cli.py`, the
existing `_apply_template` helper becomes a thin wrapper or is
replaced. Extract the copy logic into `_apply_template_files` in
`init_workflow.py`.

Append to `jig/init_workflow.py`:

```python
import shutil


def _apply_template_files(
    *,
    template_name: str,
    dest: Path,
    project_name: str,
) -> None:
    """Copy a project template into dest, substituting 'myproject'
    with a sanitized project_name. Preserves .jig/.
    """
    tpl_root = Path(__file__).resolve().parent / "defaults" / "project_templates"
    tpl_dir = tpl_root / template_name
    if not tpl_dir.is_dir():
        raise KeyError(f"unknown template: {template_name!r}")
    pkg_name = project_name.replace("-", "_").replace(" ", "_").lower()
    skip_dirs = {
        "__pycache__",
        ".ruff_cache",
        ".mypy_cache",
        ".pytest_cache",
        ".venv",
        "node_modules",
    }
    for src in tpl_dir.rglob("*"):
        if not src.is_file():
            continue
        if src.name == "template.yaml":
            continue
        rel = src.relative_to(tpl_dir)
        if skip_dirs & set(rel.parts):
            continue
        rel_renamed = Path(str(rel).replace("myproject", pkg_name))
        dest_file = dest / rel_renamed
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        raw = src.read_bytes()
        try:
            text = raw.decode()
            dest_file.write_text(text.replace("myproject", pkg_name))
        except UnicodeDecodeError:
            dest_file.write_bytes(raw)


async def apply_scaffold(
    *,
    project_path: Path,
    template_name: str,
    sa_path: bool,
    config: dict | None,
    tickets: TicketStore,
    threads: ThreadStore,
) -> None:
    """Copy the template, finalize architecture.yaml, update
    project.yaml, emit scaffold_applied.
    """
    md = load_template_metadata(template_name)
    applied_at = datetime.now(timezone.utc).isoformat()

    # 1. Copy template files.
    _apply_template_files(
        template_name=template_name,
        dest=project_path,
        project_name=project_path.name,
    )

    # 2. Finalize architecture.yaml.
    arch_file = project_path / ".jig" / "spec" / "architecture.yaml"
    if arch_file.is_file():
        data = yaml.safe_load(arch_file.read_text()) or {}
    else:
        data = {}
    data.setdefault("template", template_name)
    data["template"] = template_name
    data["template_applied_at"] = applied_at
    data["sa_path"] = sa_path
    data.setdefault("language", md.language)
    if md.framework is not None:
        data.setdefault("framework", md.framework)
    if md.deploy_target is not None:
        data.setdefault("deploy_target", md.deploy_target)
    if sa_path and config is not None:
        data["config"] = config
    atomic_write_text(arch_file, yaml.safe_dump(data, sort_keys=False))

    # 3. Update project.yaml.
    project_yaml = project_path / ".jig" / "project.yaml"
    pdata = yaml.safe_load(project_yaml.read_text()) or {}
    pdata["template_name"] = template_name
    pdata["template_applied_at"] = applied_at
    atomic_write_text(project_yaml, yaml.safe_dump(pdata, sort_keys=False))

    # 4. Emit scaffold_applied SystemEvent.
    arch = await tickets.get("architecture")
    if arch is None:
        await tickets.create(
            Ticket(
                id="architecture",
                work_type=WorkType.ARCHITECTURE,
                title="Architecture",
                created_by="cli",
            )
        )
    await threads.post(
        SystemEvent(
            ticket_id="architecture",
            author="cli",
            event_type="scaffold_applied",
            content=f"template={template_name}",
        )
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_workflow.py -v -k scaffold`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_workflow.py tests/test_init_workflow.py
git commit -m "feat(init): scaffold application with architecture.yaml finalization"
```

---

### Task 18: Resume state classifier and dispatch

**Files:**
- Modify: `jig/init_workflow.py`
- Test: `tests/test_init_workflow_resume.py`

**References:** REQ-INIT-RESUME.1–12.

- [ ] **Step 1: Write failing tests**

Create `tests/test_init_workflow_resume.py`:

```python
from pathlib import Path

import pytest

from jig.init_workflow import ResumeState, classify_resume, create_stub
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff, Note, SystemEvent
from jig.ticket import Ticket, WorkType


@pytest.fixture
async def wired(tmp_path: Path):
    create_stub(tmp_path, name="p")
    tickets = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    bus = MessageBus(tmp_path / ".jig" / "store" / "messages.jsonl")
    await tickets.load()
    await threads.load()
    await bus.load()
    return {"path": tmp_path, "tickets": tickets, "threads": threads, "bus": bus}


@pytest.mark.asyncio
async def test_resume_fresh(wired):
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.PO_CONVERSATION
    )


@pytest.mark.asyncio
async def test_resume_po_conversation_in_progress(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.PO_CONVERSATION
    )


@pytest.mark.asyncio
async def test_resume_after_handoff_before_spec(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.SPEC_GENERATION
    )


@pytest.mark.asyncio
async def test_resume_gap_prompt(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    await wired["threads"].post(
        Note(
            ticket_id="brief",
            author="spec-generator",
            text="gaps",
            payload={"gaps": [{"kind": "missing", "severity": "blocking",
                              "location": "x", "description": "d"}]},
        )
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_gaps_reported",
            content="",
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.GAP_PROMPT
    )


@pytest.mark.asyncio
async def test_resume_branch_prompt(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        Handoff(ticket_id="brief", author="po", phase="spec-generator", summary="")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="",
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.BRANCH_PROMPT
    )


@pytest.mark.asyncio
async def test_resume_sa_in_progress(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="",
        )
    )
    await wired["tickets"].create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="a",
            created_by="cli",
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.SA_CONVERSATION
    )


@pytest.mark.asyncio
async def test_resume_sa_confirm_prompt(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="",
        )
    )
    await wired["tickets"].create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="a",
            created_by="cli",
        )
    )
    await wired["threads"].post(
        Note(
            ticket_id="architecture",
            author="sa",
            text="propose",
            payload={
                "kind": "sa_propose_scaffold",
                "template_name": "python",
                "rationale": "r",
                "config": {},
            },
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.SA_CONFIRM_PROMPT
    )


@pytest.mark.asyncio
async def test_resume_direct_pick_pending(wired):
    await wired["tickets"].create(
        Ticket(id="brief", work_type=WorkType.BRIEF, title="b", created_by="cli")
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="brief",
            author="spec-generator",
            event_type="spec_generated",
            content="",
        )
    )
    await wired["tickets"].create(
        Ticket(
            id="architecture",
            work_type=WorkType.ARCHITECTURE,
            title="a",
            created_by="cli",
        )
    )
    await wired["threads"].post(
        SystemEvent(
            ticket_id="architecture",
            author="cli",
            event_type="sa_skipped",
            content="",
        )
    )
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.DIRECT_TEMPLATE_PICK
    )


@pytest.mark.asyncio
async def test_resume_already_done_detected_via_dirstate(wired):
    import yaml
    project_yaml = wired["path"] / ".jig" / "project.yaml"
    data = yaml.safe_load(project_yaml.read_text())
    data["template_applied_at"] = "2026-04-24T00:00:00Z"
    project_yaml.write_text(yaml.safe_dump(data))
    assert (
        await classify_resume(
            project_path=wired["path"],
            tickets=wired["tickets"],
            threads=wired["threads"],
        )
        == ResumeState.ALREADY_DONE
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_workflow_resume.py -v`
Expected: FAIL — `ResumeState` / `classify_resume` missing.

- [ ] **Step 3: Implement classifier**

Append to `jig/init_workflow.py`:

```python
class ResumeState(str, Enum):
    PO_CONVERSATION = "po_conversation"
    SPEC_GENERATION = "spec_generation"
    GAP_PROMPT = "gap_prompt"
    BRANCH_PROMPT = "branch_prompt"
    SA_CONVERSATION = "sa_conversation"
    SA_CONFIRM_PROMPT = "sa_confirm_prompt"
    DIRECT_TEMPLATE_PICK = "direct_template_pick"
    ALREADY_DONE = "already_done"
    BROKEN = "broken"


async def classify_resume(
    *,
    project_path: Path,
    tickets: TicketStore,
    threads: ThreadStore,
) -> ResumeState:
    """Pure inspection. Maps persisted state to the next action."""
    # Short-circuit on already-done / broken via directory state.
    ds = classify_directory(project_path)
    if ds == DirState.ALREADY_DONE:
        return ResumeState.ALREADY_DONE
    if ds == DirState.BROKEN:
        return ResumeState.BROKEN

    brief = await tickets.get("brief")
    if brief is None:
        return ResumeState.PO_CONVERSATION

    brief_entries = await threads.for_ticket("brief")
    has_handoff = any(isinstance(e, Handoff) for e in brief_entries)
    has_spec_gen_event = any(
        isinstance(e, SystemEvent) and e.event_type == "spec_generated"
        for e in brief_entries
    )
    has_gaps_event = any(
        isinstance(e, SystemEvent) and e.event_type == "spec_gaps_reported"
        for e in brief_entries
    )

    if not has_handoff:
        return ResumeState.PO_CONVERSATION
    if has_gaps_event and not has_spec_gen_event:
        return ResumeState.GAP_PROMPT
    if not has_spec_gen_event:
        return ResumeState.SPEC_GENERATION

    # Spec generated. Now look at architecture ticket.
    arch = await tickets.get("architecture")
    if arch is None:
        return ResumeState.BRANCH_PROMPT

    arch_entries = await threads.for_ticket("architecture")
    has_sa_skipped = any(
        isinstance(e, SystemEvent) and e.event_type == "sa_skipped"
        for e in arch_entries
    )
    has_scaffold_applied = any(
        isinstance(e, SystemEvent) and e.event_type == "scaffold_applied"
        for e in arch_entries
    )
    proposal = next(
        (
            e for e in arch_entries
            if isinstance(e, Note) and e.payload.get("kind") == "sa_propose_scaffold"
        ),
        None,
    )

    if has_scaffold_applied:
        return ResumeState.ALREADY_DONE
    if has_sa_skipped:
        return ResumeState.DIRECT_TEMPLATE_PICK
    if proposal is not None:
        return ResumeState.SA_CONFIRM_PROMPT
    return ResumeState.SA_CONVERSATION
```

Also add the imports at the top if not already present:

```python
from jig.thread import Handoff
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_init_workflow_resume.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/init_workflow.py tests/test_init_workflow_resume.py
git commit -m "feat(init): resume state classifier mapping persisted state to next action"
```

---

### Task 19: Wire full run_init dispatch

**Files:**
- Modify: `jig/init_workflow.py`

**References:** REQ-INIT-CLI.6/9/10/11/12/13/14/16, REQ-INIT-RESUME.1.

- [ ] **Step 1: Replace run_init with the full dispatch**

Edit `jig/init_workflow.py`, replace the stub `run_init` with:

```python
async def run_init(*, name: str, force: bool) -> None:
    """Top-level init flow. Dispatches fresh vs resume by classification."""
    target = Path(name)
    ds = classify_directory(target)
    if ds == DirState.ALREADY_DONE and not force:
        raise click.ClickException(
            f"{target} already initialized. Use --force to restart from scratch."
        )
    if ds == DirState.BROKEN and not force:
        raise click.ClickException(
            f"{target}/.jig is in an inconsistent state. Use --force to reset."
        )
    if force and (target / ".jig").is_dir():
        _confirm_force(target)
        shutil.rmtree(target / ".jig")

    create_stub(target, name=name)
    store_dir = target / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    memory = MemoryStore(store_dir)
    bus = MessageBus(store_dir / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()

    # Iterate: each pass classifies the resume state and advances one step.
    while True:
        rs = await classify_resume(
            project_path=target, tickets=tickets, threads=threads
        )
        if rs == ResumeState.ALREADY_DONE:
            _print_already_done(target)
            return
        if rs == ResumeState.BROKEN:
            raise click.ClickException(
                f"{target}/.jig is inconsistent. Use --force to reset."
            )
        if rs == ResumeState.PO_CONVERSATION:
            await run_po_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.SPEC_GENERATION:
            from jig.spec_generator import run_spec_generator
            await run_spec_generator(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.GAP_PROMPT:
            decision = await prompt_gap_decision(threads)
            if decision == "Q":
                click.echo("State saved. Resume later with `jig init <name>`.")
                return
            # Resume-PO: drop the gap SystemEvent marker? Simpler: the
            # PO re-spawn will re-run, and when po_finish_brief fires,
            # spec-gen will re-run. classify_resume returns PO because
            # there's no new Handoff; PO will post a new one on finish.
            await run_po_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.BRANCH_PROMPT:
            choice = await prompt_branch_choice()
            if choice == BranchChoice.STAY:
                await run_po_conversation(
                    project_path=target, tickets=tickets,
                    threads=threads, memory=memory, bus=bus,
                )
                continue
            if choice == BranchChoice.DIRECT:
                await create_sa_skipped_marker(
                    tickets=tickets, threads=threads
                )
                continue
            # SA: create ticket (if needed) and run.
            await run_sa_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.SA_CONVERSATION:
            await run_sa_conversation(
                project_path=target, tickets=tickets,
                threads=threads, memory=memory, bus=bus,
            )
            continue
        if rs == ResumeState.SA_CONFIRM_PROMPT:
            decision, proposal = await prompt_sa_confirm(threads)
            assert proposal is not None
            if decision == ConfirmChoice.NO:
                click.echo("Scaffold cancelled. State saved.")
                return
            if decision == ConfirmChoice.SWAP:
                # Re-spawn SA; the existing proposal remains in the
                # thread so SA sees the prior decision.
                await run_sa_conversation(
                    project_path=target, tickets=tickets,
                    threads=threads, memory=memory, bus=bus,
                )
                continue
            # YES: scaffold with SA's proposal.
            await apply_scaffold(
                project_path=target,
                template_name=proposal["template_name"],
                sa_path=True,
                config=proposal.get("config", {}),
                tickets=tickets, threads=threads,
            )
            _print_summary(target, template_name=proposal["template_name"])
            return
        if rs == ResumeState.DIRECT_TEMPLATE_PICK:
            tpl = await prompt_direct_template()
            await apply_scaffold(
                project_path=target,
                template_name=tpl,
                sa_path=False,
                config=None,
                tickets=tickets, threads=threads,
            )
            _print_summary(target, template_name=tpl)
            return
        raise RuntimeError(f"unreachable resume state: {rs}")


def _print_already_done(target: Path) -> None:
    click.echo(
        f"{target} is already initialized. Next: run `jig start` here."
    )


def _print_summary(target: Path, *, template_name: str) -> None:
    click.echo(
        f"\n"
        f"Brief:        {target}/docs/brief.md\n"
        f"Spec:         {target}/.jig/spec/project.structured.yaml\n"
        f"Architecture: {target}/.jig/spec/architecture.yaml\n"
        f"Template:     {template_name}\n\n"
        f"Setup log:    jig story brief\n"
        f"              jig story architecture\n"
    )
```

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest tests/ -v`
Expected: PASS across the board. No unit test exercises run_init
end-to-end yet (that's task 20), but all the component tests should
still pass.

- [ ] **Step 3: Commit**

```bash
git add jig/init_workflow.py
git commit -m "feat(init): wire full run_init dispatch over resume states"
```

---

### Task 20: End-to-end integration tests

**Files:**
- Create: `tests/test_init_workflow_e2e.py`

**References:** REQ-INIT-CLI.13, REQ-INIT-SCAFFOLD.6, REQ-INIT-RESUME.1.

These tests stub the agent spawn with a fake that simulates PO /
spec-generator / SA actions against the stores, so the CLI
dispatch can be exercised without launching real Claude agents.

- [ ] **Step 1: Write failing E2E tests**

Create `tests/test_init_workflow_e2e.py`:

```python
"""End-to-end init workflow tests with a mock agent runner."""
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from jig.init_mcp import (
    handle_arch_set_field,
    handle_po_finish_brief,
    handle_sa_propose_scaffold,
    handle_spec_publish,
)
from jig.init_workflow import run_init
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


class FakeAgent:
    """Dispatch table: each (project_path, role, ticket_id) tuple maps to
    a coroutine that simulates that agent's side effects before exit.
    """

    def __init__(self):
        self._handlers: dict[tuple[str, str], callable] = {}

    def handle(self, role: str, ticket_id: str):
        def deco(fn):
            self._handlers[(role, ticket_id)] = fn
            return fn
        return deco

    async def run(self, **kwargs):
        key = (kwargs["role"], kwargs["ticket_id"])
        if key in self._handlers:
            await self._handlers[key](**kwargs)


@pytest.mark.asyncio
async def test_e2e_happy_path_with_sa(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(**kw):
        # Pretend the user wrote a brief and finished.
        proj = kw["project_path"]
        (proj / ".jig" / "spec" / "project.md").write_text(
            "# proj\n\nintro\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            tickets=kw["tickets"], threads=kw["threads"], bus=kw["bus"],
            project_path=proj, summary="done", author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(**kw):
        await handle_spec_publish(
            tickets=kw["tickets"], threads=kw["threads"], bus=kw["bus"],
            project_path=kw["project_path"],
            yaml_content="name: proj\ncapabilities:\n  X: {}\n",
            advisory_notes=[],
            author="spec-generator",
        )

    @agent.handle(role="sa", ticket_id="architecture")
    async def _sa(**kw):
        proj = kw["project_path"]
        await handle_arch_set_field(
            tickets=kw["tickets"], threads=kw["threads"],
            project_path=proj, path="rationale",
            value="simple python CLI is enough", author="sa",
        )
        await handle_sa_propose_scaffold(
            tickets=kw["tickets"], threads=kw["threads"], bus=kw["bus"],
            project_path=proj, template_name="python",
            rationale="simple python CLI is enough", config={},
            author="sa",
        )

    # Auto-accept prompts: branch=Y, confirm=Y.
    answers = iter(["Y", "Y"])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with patch("jig.init_workflow.run_agent_for_ticket", new=agent.run):
        await run_init(name="proj", force=False)

    project = tmp_path / "proj"
    assert (project / ".jig" / "spec" / "project.md").is_file()
    assert (project / ".jig" / "spec" / "project.structured.yaml").is_file()
    assert (project / ".jig" / "spec" / "architecture.yaml").is_file()
    arch = yaml.safe_load(
        (project / ".jig" / "spec" / "architecture.yaml").read_text()
    )
    assert arch["template"] == "python"
    assert arch["sa_path"] is True
    assert arch["rationale"] == "simple python CLI is enough"


@pytest.mark.asyncio
async def test_e2e_direct_path(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(**kw):
        proj = kw["project_path"]
        (proj / ".jig" / "spec" / "project.md").write_text(
            "# proj\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            tickets=kw["tickets"], threads=kw["threads"], bus=kw["bus"],
            project_path=proj, summary="done", author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(**kw):
        await handle_spec_publish(
            tickets=kw["tickets"], threads=kw["threads"], bus=kw["bus"],
            project_path=kw["project_path"],
            yaml_content="name: proj\n", advisory_notes=[],
            author="spec-generator",
        )

    # branch=p, template pick=1 (python).
    answers = iter(["p", "1"])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with patch("jig.init_workflow.run_agent_for_ticket", new=agent.run):
        await run_init(name="directproj", force=False)

    project = tmp_path / "directproj"
    arch = yaml.safe_load(
        (project / ".jig" / "spec" / "architecture.yaml").read_text()
    )
    assert arch["sa_path"] is False
    assert "rationale" not in arch
    assert arch["language"] == "python"


@pytest.mark.asyncio
async def test_e2e_resume_after_spec_generation(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    # First pass: PO writes brief + finish, then spec-generator succeeds,
    # then user picks Quit at branch prompt (simulated as s→no wait,
    # we want to quit; simpler: use direct path 'p' then abort the
    # template pick via KeyboardInterrupt? Cleaner: just let the first
    # run reach BRANCH_PROMPT and choose 's' to stay, but we want a
    # clean exit. Use 'p' + '1' to finish first run.)
    #
    # Actually for a resume test, the simplest approach is to stop the
    # first run mid-flow by raising from a handler.

    @agent.handle(role="po", ticket_id="brief")
    async def _po(**kw):
        proj = kw["project_path"]
        (proj / ".jig" / "spec" / "project.md").write_text(
            "# proj\n\n## Planned (committed)\n\n### X\nprose\n"
        )
        await handle_po_finish_brief(
            tickets=kw["tickets"], threads=kw["threads"], bus=kw["bus"],
            project_path=proj, summary="done", author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(**kw):
        await handle_spec_publish(
            tickets=kw["tickets"], threads=kw["threads"], bus=kw["bus"],
            project_path=kw["project_path"],
            yaml_content="name: proj\n", advisory_notes=[],
            author="spec-generator",
        )

    # First run: branch=s will send us back to PO — but we already
    # have a Handoff, so PO gets re-spawned; our _po handler would
    # post a second Handoff, triggering another spec-gen, etc.
    # To get a clean interrupt, raise SystemExit from the branch
    # prompt and then re-invoke run_init.
    first_answers = iter(["p"])
    second_answers = iter(["p", "1"])

    call_count = {"n": 0}

    def fake_prompt(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] <= 1:
            # First run reaches branch prompt; raise to simulate Ctrl-C.
            raise KeyboardInterrupt
        return next(second_answers)

    with patch("jig.init_workflow.run_agent_for_ticket", new=agent.run):
        monkeypatch.setattr("click.prompt", fake_prompt)
        with pytest.raises(KeyboardInterrupt):
            await run_init(name="resumeproj", force=False)
        # Second run: resume from BRANCH_PROMPT.
        await run_init(name="resumeproj", force=False)

    project = tmp_path / "resumeproj"
    assert (project / ".jig" / "spec" / "architecture.yaml").is_file()
    arch = yaml.safe_load(
        (project / ".jig" / "spec" / "architecture.yaml").read_text()
    )
    assert arch["template"] == "python"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_init_workflow_e2e.py -v`
Expected: FAIL — likely signature mismatch on the mocked
`run_agent_for_ticket`. The test informs what the real signature
should look like; update the FakeAgent dispatch to match.

- [ ] **Step 3: Resolve signature mismatches**

If `run_agent_for_ticket` in `jig/agent.py` does not accept kwargs
`project_path`, `role`, `ticket_id`, `tickets`, `threads`, `memory`,
`bus`, add a thin wrapper in `jig/init_workflow.py` that adapts to
its actual signature — or change the signature to match. The
wrapper approach keeps the callers in the init workflow uniform.

- [ ] **Step 4: Run E2E tests to verify pass**

Run: `uv run pytest tests/test_init_workflow_e2e.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the complete suite**

Run: `uv run pytest tests/ -v`
Expected: PASS.

- [ ] **Step 6: Run the linter**

Run: `uv run ruff check jig/`
Expected: no new violations.

- [ ] **Step 7: Commit**

```bash
git add tests/test_init_workflow_e2e.py jig/init_workflow.py
git commit -m "test(init): end-to-end SA, direct-path, and resume flows"
```

---

### Task 21: `jig story brief` / `jig story architecture` smoke test

**Files:**
- Test: `tests/test_init_workflow_e2e.py` (extend)

**References:** Problem success-criterion — "`jig story brief` and
`jig story architecture` show the full setup trail".

- [ ] **Step 1: Add smoke test for story**

Append to `tests/test_init_workflow_e2e.py`:

```python
import asyncio

from jig.story import build_story


@pytest.mark.asyncio
async def test_story_brief_contains_po_and_specgen_trail(tmp_path: Path, monkeypatch):
    """After an E2E happy path, jig story brief should show the
    Handoff, spec_generated event, and any Notes.
    """
    monkeypatch.chdir(tmp_path)
    agent = FakeAgent()

    @agent.handle(role="po", ticket_id="brief")
    async def _po(**kw):
        proj = kw["project_path"]
        (proj / ".jig" / "spec" / "project.md").write_text(
            "# proj\n\n## Planned (committed)\n\n### X\n"
        )
        await handle_po_finish_brief(
            tickets=kw["tickets"], threads=kw["threads"], bus=kw["bus"],
            project_path=proj, summary="done", author="po",
        )

    @agent.handle(role="spec-generator", ticket_id="brief")
    async def _sg(**kw):
        await handle_spec_publish(
            tickets=kw["tickets"], threads=kw["threads"], bus=kw["bus"],
            project_path=kw["project_path"],
            yaml_content="name: proj\n",
            advisory_notes=["watch out for X"],
            author="spec-generator",
        )

    answers = iter(["p", "1"])
    monkeypatch.setattr("click.prompt", lambda *a, **kw: next(answers))

    with patch("jig.init_workflow.run_agent_for_ticket", new=agent.run):
        await run_init(name="storyproj", force=False)

    project = tmp_path / "storyproj"
    store = project / ".jig" / "store"
    tickets = TicketStore(store / "tickets.jsonl")
    threads = ThreadStore(store / "comments.jsonl")
    await tickets.load()
    await threads.load()
    events = await build_story(
        "brief", project_path=project, threads=threads, tickets=tickets
    )
    kinds = [e.kind for e in events]
    assert "handoff" in kinds
    assert any("spec_generated" in e.message for e in events)
    assert any("watch out for X" in e.message for e in events)

    arch_events = await build_story(
        "architecture", project_path=project, threads=threads, tickets=tickets
    )
    assert any("sa_skipped" in e.message for e in arch_events)
    assert any("scaffold_applied" in e.message for e in arch_events)
```

- [ ] **Step 2: Run to verify pass**

Run: `uv run pytest tests/test_init_workflow_e2e.py::test_story_brief_contains_po_and_specgen_trail -v`
Expected: PASS.

- [ ] **Step 3: Full suite + lint + type check**

Run: `uv run pytest tests/ -v && uv run ruff check jig/ tests/`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_init_workflow_e2e.py
git commit -m "test(init): jig story smoke test covering brief and architecture trails"
```

---

## Rollback

If this work is abandoned mid-stream, safe rollback is:

1. Delete the new files listed under "New files" above.
2. Revert the modifications to `jig/ticket.py`, `jig/thread.py`,
   `jig/mcp_server.py`, `jig/cli.py`, and `jig/store/tickets.py`.
3. `uv run pytest tests/ -v` on main should return to its pre-branch
   state.

The data model extensions (WorkType values, SystemEvent.event_type
literal additions, Note.payload field) are additive and backward-
compatible with existing JSONL stores — no migration is required and
reverting them does not corrupt stores that never wrote the new
values.

If partial state has been written to a user's project (`project.md`,
`project.structured.yaml`, `architecture.yaml`, brief/architecture
tickets in the JSONL stores), the user's recovery path is
`jig init <name> --force` on the affected directory, which wipes
`.jig/` and restarts. No jig-side migration is required.

## Out of scope for this plan

- Reactive spec agent (file-watching, drift detection,
  auto-regeneration on out-of-band brief edits). Separate sub-project.
- PM role, issues, plans, capability specs, worker agent flows.
  Separate sub-project.
- Template-context library (downstream context hydration that uses
  architecture.yaml to compose agent context). Separate sub-project.
- Template specification (directory layout, config-parameter schema,
  scaffold hooks). This plan uses an informal convention and the
  metadata schema introduced in Task 4.
- Transactional scaffolding with rollback. Half-applied scaffold
  errors with `--force` as the only escape hatch.
- TUI parity. CLI-only in v1.
- `--reset-init` flag and other safer-than-`--force` escape hatches.
- Streaming progress UI during spec generation.
- Override mechanism for spec-generator findings.

## Change log

- 2026-04-24: Initial draft (brent)
