---
title: caderon-pack — Implementation Plan
type: plan
status: active
owner: brent
created: 2026-05-25
updated: 2026-05-25
design: ./design.md
---

# caderon-pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the `caderon-pack` Claude Code plugin containing the `start-feature` skill, install
it as a local plugin, and wire it into chezmoi for cross-machine deployment.

**Architecture:** Standalone git repo following the Claude Code plugin spec. One skill
(`start-feature`) with five template files. Plugin registered as a local Claude Code marketplace
entry, then installed from it. Chezmoi `.chezmoiexternal.toml` clones the repo on new machines.

**Tech Stack:** Markdown, JSON, Claude Code plugin system, `claude plugins` CLI, git, chezmoi.

---

## Preconditions

- [ ] GitHub repo `https://github.com/brent-hoover/caderon-pack` created and empty
- [ ] `gh` CLI authenticated
- [ ] `claude` CLI accessible in shell
- [ ] `chezmoi` installed and source at `~/.local/share/chezmoi`

---

## Task 1: Initialize the repo

**Files:**
- Create: `~/Projects/personal/caderon-pack/` (repo root)

- [ ] **Step 1: Clone the empty GitHub repo**

```bash
cd ~/Projects/personal
gh repo clone brent-hoover/caderon-pack
cd caderon-pack
```

Expected: empty repo cloned, on `main` branch.

- [ ] **Step 2: Create the directory skeleton**

```bash
mkdir -p .claude-plugin
mkdir -p skills/start-feature/templates
```

- [ ] **Step 3: Verify structure**

```bash
find . -type d | sort
```

Expected output:
```
.
./.git
./.claude-plugin
./skills
./skills/start-feature
./skills/start-feature/templates
```

---

## Task 2: Write plugin manifest and package.json

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `package.json`

- [ ] **Step 1: Write plugin.json**

```bash
cat > .claude-plugin/plugin.json << 'EOF'
{
  "name": "caderon-pack",
  "version": "1.0.0",
  "description": "Personal Claude Code plugin pack — skills, commands, and hooks for Brent's workflow",
  "author": {
    "name": "Brent Hoover",
    "email": "brent@thebuddhalodge.com"
  },
  "homepage": "https://github.com/brent-hoover/caderon-pack",
  "repository": "https://github.com/brent-hoover/caderon-pack",
  "license": "MIT",
  "keywords": ["personal", "workflow", "feature-docs", "start-feature"]
}
EOF
```

- [ ] **Step 2: Write package.json** (npm-ready for future publishing)

```bash
cat > package.json << 'EOF'
{
  "name": "caderon-pack",
  "version": "1.0.0",
  "description": "Personal Claude Code plugin pack",
  "type": "module",
  "files": [
    "README.md",
    ".claude-plugin/",
    "skills/"
  ],
  "keywords": [
    "claude-code",
    "claude-code-plugin",
    "workflow",
    "feature-docs"
  ],
  "author": {
    "name": "Brent Hoover",
    "email": "brent@thebuddhalodge.com"
  },
  "license": "MIT",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/brent-hoover/caderon-pack.git"
  },
  "homepage": "https://github.com/brent-hoover/caderon-pack",
  "publishConfig": {
    "access": "public"
  }
}
EOF
```

- [ ] **Step 3: Commit**

```bash
git add .claude-plugin/plugin.json package.json
git commit -m "chore: add plugin manifest and package.json"
```

---

## Task 3: Write the problem.md template

**Files:**
- Create: `skills/start-feature/templates/problem.md`

- [ ] **Step 1: Write the template**

```bash
cat > skills/start-feature/templates/problem.md << 'TEMPLATE'
---
title: <Feature Name> — Problem Statement
type: problem
status: draft
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# <Feature Name> — Problem Statement

## Context

<What's the current situation? What's in place today, and what's prompting this work? One or two
paragraphs. Orient someone who's never seen this problem before.>

## Problem

<What specifically is wrong, missing, or needed? Be concrete. Avoid proposing solutions here —
that's the design doc's job.>

## Simplest possible solution

<Before considering complications, what's the most obvious, dumbest thing that would solve the
problem as stated? Not the elegant answer; the *simplest* answer. This section exists to catch
over-engineering at the source.>

## Complications considered

<For each complication, state: does it actually apply, and if so, what does it force? Mark
non-applicable ones "N/A: <why>" rather than skipping — silence is indistinguishable from
"we didn't think about it.">

- **Scale**: <grows non-linearly with users, data volume, or load? If not: "N/A — bounded by <thing>".>
- **Concurrency**: <multiple writers or race conditions? If not: "N/A — single-writer / serialized".>
- **Failure modes**: <what breaks if the simplest solution fails? If trivially handled: "N/A — fail-loud".>
- **Cross-cutting policies**: <PII, auth, secrets, audit, observability? If none: "N/A — touches none".>

## Constraints

<What are we operating under that constrains any solution?>

-
-

## Requirements

<The "must be true" statements. Prefer observable, checkable statements.>

-
-

## Non-goals

<What is explicitly OUT of scope?>

-
-

## Success criteria

<How will we know the work is done and the problem is solved?>

-
-

## Open questions

<Things that need answers before design can start.>

- [ ]

## Change log

- YYYY-MM-DD: Initial draft (<username>)
TEMPLATE
```

- [ ] **Step 2: Verify**

```bash
head -5 skills/start-feature/templates/problem.md
```

Expected: YAML frontmatter starting with `---`.

---

## Task 4: Write the design.md template

**Files:**
- Create: `skills/start-feature/templates/design.md`

- [ ] **Step 1: Write the template**

```bash
cat > skills/start-feature/templates/design.md << 'TEMPLATE'
---
title: <Feature Name> — Design
type: design
status: draft
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
problem: ./problem.md
---

# <Feature Name> — Design

## Summary

<One paragraph. What is the proposed approach? Someone reading only this section should know
roughly what we're going to build.>

## Approach

<The chosen design, in enough detail to implement against. Cover the main components, their
responsibilities, and how they interact. Diagrams welcome.>

## Interfaces

<External-facing APIs, CLI surfaces, file formats, wire protocols. Changes to these are
expensive later, so pin them down now.>

## Data model

<If there's persistent state or structured data, describe its shape. Skip if none.>

## Alternatives considered

<What else did we look at, and why did we not choose it? One paragraph per alternative,
including the chosen one.>

### <Alternative 1>

<What it was, why we rejected it.>

### <Alternative 2>

<What it was, why we rejected it.>

### Chosen: <the chosen approach>

<Why this one won.>

## Risks

<What could go wrong? What assumptions, if invalidated, break this design?>

-
-

## Out of scope

<What this design does NOT address, even though it might seem related.>

-
-

## Open questions

<Design-level questions still unresolved. Blocking questions must be answered before
implementation starts.>

- [ ]

## Change log

- YYYY-MM-DD: Initial draft (<username>)
TEMPLATE
```

---

## Task 5: Write the plan.md template

**Files:**
- Create: `skills/start-feature/templates/plan.md`

- [ ] **Step 1: Write the template**

```bash
cat > skills/start-feature/templates/plan.md << 'TEMPLATE'
---
title: <Feature Name> — Implementation Plan
type: plan
status: draft
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
design: ./design.md
---

# <Feature Name> — Implementation Plan

## Overview

<One paragraph. What are we implementing, in what order, and why that order. This doc is
throwaway — it exists to coordinate the work, not to document the system.>

## Preconditions

<What must be true before we start? Approved design, resolved open questions, dependencies.>

- [ ]
- [ ]

## Steps

<Ordered list. Each step should be small enough to fit in one PR or one session.>

### 1. <Step name>

**What:** <Concrete change — files touched, behavior added/modified.>

**Why:** <What this step unblocks or achieves.>

**Verify:** <How we confirm it worked. Tests, manual check, command output.>

### 2. <Step name>

**What:**

**Why:**

**Verify:**

## Rollback

<If this goes sideways mid-way, how do we get back to a safe state?>

## Out of scope for this plan

<Things we explicitly are NOT doing as part of this work.>

-
-

## Change log

- YYYY-MM-DD: Initial draft (<username>)
TEMPLATE
```

---

## Task 6: Write the deferred.md template

**Files:**
- Create: `skills/start-feature/templates/deferred.md`

- [ ] **Step 1: Write the template**

```bash
cat > skills/start-feature/templates/deferred.md << 'TEMPLATE'
---
title: <Feature Name> — Deferred Work
type: notes
status: active
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# <Feature Name> — Deferred Work

Items from the implementation plan that were intentionally not implemented. Review this doc
at project close to confirm nothing critical was silently dropped.

## Deferred items

### <Item name>

**Originally planned:** <What was described in the plan.>

**Not implemented because:** <Specific reason — over-engineered for current needs, blocked by X,
decided to simplify, descoped after discussion with <person>.>

**Impact:** <Critical / nice-to-have / can be permanently dropped?>

**Revisit when:** <Condition or date. "Never — permanently descoped" is a valid answer.>

---

## Change log

- YYYY-MM-DD: Initial draft (<username>)
TEMPLATE
```

---

## Task 7: Write the completed.md template

**Files:**
- Create: `skills/start-feature/templates/completed.md`

- [ ] **Step 1: Write the template**

```bash
cat > skills/start-feature/templates/completed.md << 'TEMPLATE'
---
title: <Feature Name> — Completion Record
type: notes
status: active
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

# <Feature Name> — Completion Record

Handoff manifest for everything shipped as part of this feature. Authoritative record of what
is now in the codebase as a result of this work.

## What shipped

<One paragraph summary of what was built.>

## New modules / files

<Every new file or module added, with a one-line description of what it does.>

-

## Modified files

<Significant changes to existing files and what changed.>

-

## Dependencies added

<New packages, libraries, or services introduced. Include versions.>

-

## Interface changes

<Changes to APIs, CLIs, file formats, or protocols that other code or people depend on.>

-

## Configuration changes

<New env vars, config files, feature flags, or settings operators must know about.>

-

## Known issues / follow-ups

<Bugs found but not fixed, things to watch, anything left rough.>

-

## Deferred work

<Link to deferred.md if one exists, or "none".>

## Next steps

<What happens next? Who owns it?>

-

## Archiving

When this record is complete, move the feature directory to `<doc-root>/archived/<slug>/`
to keep the doc root reflecting only in-progress work.

## Change log

- YYYY-MM-DD: Completed (<username>)
TEMPLATE
```

- [ ] **Step 2: Commit all templates**

```bash
git add skills/start-feature/templates/
git commit -m "feat: add doc templates (problem, design, plan, deferred, completed)"
```

- [ ] **Step 3: Verify all five templates exist**

```bash
ls -1 skills/start-feature/templates/
```

Expected:
```
completed.md
deferred.md
design.md
plan.md
problem.md
```

---

## Task 8: Write SKILL.md

**Files:**
- Create: `skills/start-feature/SKILL.md`

- [ ] **Step 1: Write SKILL.md**

```bash
cat > skills/start-feature/SKILL.md << 'SKILL'
---
name: start-feature
description: >
  Guides through the full problem → design → plan documentation workflow for a new feature.
  Use when starting any new feature, initiative, or significant piece of work.
  Trigger phrases: "start a feature", "new feature", "/start-feature", "/start-feature <slug>".
  Claude drives content generation; user reviews and approves each doc before advancing.
version: 1.0.0
allowed-tools: Read, Write, Bash, Glob
---

# start-feature

Four-phase state machine: PROBLEM → DESIGN (optional) → PLAN → DONE. Each phase produces a
fully-populated doc. No `<placeholder>` text is left for the user to fill in.

---

## Setup

Before entering any phase:

**1. Get the feature slug.**

Check if an argument was passed (e.g., `/start-feature my-feature`). If yes, use it. If not,
ask: "What's the feature slug? Use kebab-case — e.g. `user-auth`, `billing-export`."

**2. Detect doc convention** by reading `CLAUDE.md` with the Read tool:

- Mentions `feature-work/` → doc root is `feature-work/`
- Otherwise → doc root is `docs/`

Announce: `Writing docs to <doc-root>/<slug>/`

Create the directory:

```bash
mkdir -p <doc-root>/<slug>
```

**3. Get metadata:**

```bash
date +%Y-%m-%d        # today
git config user.name  # owner
```

**4. Read the three pipeline templates** (needed for reference throughout):

- `${CLAUDE_PLUGIN_ROOT}/skills/start-feature/templates/problem.md`
- `${CLAUDE_PLUGIN_ROOT}/skills/start-feature/templates/design.md`
- `${CLAUDE_PLUGIN_ROOT}/skills/start-feature/templates/plan.md`

---

## PHASE: PROBLEM

Announce: **[PHASE: PROBLEM]**

Ask these questions **one at a time**. Wait for the complete answer before asking the next.

1. "What's the human-readable name for this feature?"
2. "What's the current situation? What's in place today that prompted this work?"
3. "What specifically is wrong, missing, or needed? Be concrete — no solutions yet."
4. "What's the simplest possible solution — the dumbest thing that would technically work?"
5. "Which complications apply? For each, say whether it applies and what it forces:
   - **Scale**: grows non-linearly with users/data/load?
   - **Concurrency**: multiple writers or race conditions?
   - **Failure modes**: what breaks if the simplest solution fails?
   - **Cross-cutting policies**: PII, auth, secrets, observability?"
6. "What are the hard constraints? (performance, environment, integrations, timeline)"
7. "What's explicitly out of scope?"
8. "How will we know this is done? What does success look like?"

After collecting answers, read any related existing files (feature docs, source modules) to
ground the draft in real project context.

Draft `problem.md` from the template. Fill every section. Replace `<username>` with the git
owner, `YYYY-MM-DD` with today's date, and feature name throughout.

Present the draft. Ask: "Does this problem.md look right? Any changes?"

Revise until approved. Write to `<doc-root>/<slug>/problem.md`.

**Transition question:**

> "Is this feature complex enough to warrant a design doc, or is the approach already obvious?
> (Trivial → we skip straight to the plan.)"

- Trivial → jump to **[PHASE: PLAN]**
- Not trivial → continue to **[PHASE: DESIGN]**

---

## PHASE: DESIGN

Announce: **[PHASE: DESIGN]**

Read relevant project files — existing modules, patterns, anything the feature will touch —
to ground the design in real context before proposing approaches.

**Propose 2–3 approaches.** For each:

- Name (one phrase)
- Description (2–3 sentences)
- Main trade-off
- Whether you recommend it and why

Ask: "Which approach would you like to go with?"

Then ask, one at a time:

1. "Any interfaces or APIs this design must expose or conform to?"
2. "Any data that needs to persist, and if so in what shape?"
3. "Any risks or assumptions to call out explicitly?"

Draft `design.md` from the template. The **Alternatives considered** section must include every
approach proposed above plus the chosen one with rationale. Set `problem: ./problem.md` in
frontmatter.

Present the draft. Ask: "Does this design.md look right? Any changes?"

Revise until approved. Write to `<doc-root>/<slug>/design.md`.

---

## PHASE: PLAN

Announce: **[PHASE: PLAN]**

Draft `plan.md` from the template. The plan must contain:

- **Overview**: what we're implementing, in what order, why that order (one paragraph)
- **Preconditions**: approved design, resolved open questions, dependencies available
- **Steps**: ordered, each sized for one PR or session, each with:
  - **What**: concrete change — files touched, behavior added/modified
  - **Why**: what this step unblocks or achieves
  - **Verify**: how to confirm it worked (tests, command, manual check)

Set frontmatter to `design: ./design.md` (or `design: ./problem.md` if design was skipped).

Present the draft. Ask: "Does this plan.md look right? Any changes?"

Revise until approved. Write to `<doc-root>/<slug>/plan.md`.

---

## PHASE: DONE

Announce: **[PHASE: DONE]**

```bash
git add <doc-root>/<slug>/
git commit -m "docs(<slug>): add problem/design/plan"
```

Print summary:

```
✓ <doc-root>/<slug>/problem.md
✓ <doc-root>/<slug>/design.md     (or "— skipped (trivial feature)")
✓ <doc-root>/<slug>/plan.md
Committed: docs(<slug>): add problem/design/plan
```

---

## Additional templates (outside the main pipeline)

**deferred.md** — write when a planned item is intentionally skipped during implementation.
Document what was skipped and the specific reason. Review at project close.

```bash
# Claude reads template from:
# ${CLAUDE_PLUGIN_ROOT}/skills/start-feature/templates/deferred.md
```

**completed.md** — write when the feature ships. The handoff manifest: new modules,
dependencies, interface changes, follow-ups.

```bash
# Claude reads template from:
# ${CLAUDE_PLUGIN_ROOT}/skills/start-feature/templates/completed.md
```

**Archiving**: once `completed.md` is written, move `<doc-root>/<slug>/` to
`<doc-root>/archived/<slug>/` so the doc root reflects only current in-progress work.
SKILL
```

- [ ] **Step 2: Verify frontmatter parses**

```bash
head -10 skills/start-feature/SKILL.md
```

Expected: starts with `---` and contains `name: start-feature`.

- [ ] **Step 3: Commit**

```bash
git add skills/start-feature/SKILL.md
git commit -m "feat: add start-feature skill with state machine"
```

---

## Task 9: Write README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README**

```bash
cat > README.md << 'README'
# caderon-pack

Personal Claude Code plugin pack. Skills, commands, and hooks for Brent's workflow.

## Skills

### start-feature

Guides Claude through the full `problem → design → plan` documentation workflow for a new
feature. Four-phase state machine with approval gates. Claude generates content from targeted
questions; you review and approve each doc before advancing.

**Trigger:** Say "start a feature", "new feature", or `/start-feature [slug]`.

**Output:** `problem.md`, `design.md` (unless trivial), `plan.md` in your project's doc
directory.

**Templates included:** problem, design, plan, deferred, completed.

## Installation

### Local install (any machine)

```bash
git clone https://github.com/brent-hoover/caderon-pack ~/path/to/caderon-pack
claude plugins marketplace add ~/path/to/caderon-pack
claude plugins install caderon-pack
```

Restart Claude Code to activate.

### Via chezmoi (optional — for cross-machine sync)

Add to `~/.local/share/chezmoi/.chezmoiexternal.toml`:

```toml
[".claude/plugins/caderon-pack"]
  type = "git-repo"
  url = "https://github.com/brent-hoover/caderon-pack.git"
  refreshPeriod = "168h"
```

Then register and install once per machine:

```bash
chezmoi apply
claude plugins marketplace add ~/.claude/plugins/caderon-pack
claude plugins install caderon-pack
```

## Doc conventions

The `start-feature` skill auto-detects your project's doc root from `CLAUDE.md`:

- Projects with `feature-work/` convention → writes to `feature-work/<slug>/`
- All others → writes to `docs/<slug>/`

When a feature is complete, write `completed.md` then move the directory to
`<doc-root>/archived/<slug>/`.

## Future

- npm publishing (`npm install -g caderon-pack`)
- Additional skills and commands as needed
README
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with installation and usage"
```

---

## Task 10: Push to GitHub

- [ ] **Step 1: Push**

```bash
git push -u origin main
```

- [ ] **Step 2: Verify on GitHub**

```bash
gh repo view brent-hoover/caderon-pack --web
```

Expected: repo page shows all committed files.

---

## Task 11: Register and install as local plugin

- [ ] **Step 1: Add local marketplace**

```bash
claude plugins marketplace add ~/Projects/personal/caderon-pack
```

- [ ] **Step 2: Install the plugin**

```bash
claude plugins install caderon-pack
```

- [ ] **Step 3: Verify it appears in the plugin list**

```bash
claude plugins list
```

Expected: `caderon-pack` appears with status `enabled`.

- [ ] **Step 4: Restart Claude Code**

Close and reopen the Claude Code session (or start a new one).

- [ ] **Step 5: Verify skill is available**

In a new Claude Code session, check that `start-feature` appears in the available skills list
in the system reminder at session start.

---

## Task 12: Chezmoi integration

**Files:**
- Modify: `~/.local/share/chezmoi/.chezmoiexternal.toml` (create if absent)

- [ ] **Step 1: Check for existing .chezmoiexternal.toml**

```bash
cat ~/.local/share/chezmoi/.chezmoiexternal.toml 2>/dev/null || echo "file does not exist"
```

- [ ] **Step 2: Add caderon-pack external entry**

If the file does not exist, create it. If it exists, append the entry.

```toml
[".claude/plugins/caderon-pack"]
  type = "git-repo"
  url = "https://github.com/brent-hoover/caderon-pack.git"
  refreshPeriod = "168h"
```

```bash
cat >> ~/.local/share/chezmoi/.chezmoiexternal.toml << 'TOML'

[".claude/plugins/caderon-pack"]
  type = "git-repo"
  url = "https://github.com/brent-hoover/caderon-pack.git"
  refreshPeriod = "168h"
TOML
```

- [ ] **Step 3: Verify chezmoi sees the external**

```bash
chezmoi dump-config 2>/dev/null | grep -A3 caderon || chezmoi status 2>&1 | head -20
```

Alternative verification:
```bash
cat ~/.local/share/chezmoi/.chezmoiexternal.toml | grep -A4 caderon
```

Expected: the `[".claude/plugins/caderon-pack"]` block is present.

- [ ] **Step 4: Commit the chezmoi change**

```bash
cd ~/.local/share/chezmoi
git add .chezmoiexternal.toml
git commit -m "feat: add caderon-pack as chezmoi external"
git push
```

---

## Task 13: Smoke test the skill

- [ ] **Step 1: Open a new Claude Code session in any project**

- [ ] **Step 2: Ask Claude to start a trivial feature**

Say: "start a feature called test-smoke-check"

Expected: Claude announces `[PHASE: PROBLEM]`, asks the problem questions one at a time, drafts
`problem.md`, asks whether design is needed, skips to `[PHASE: PLAN]` on "trivial", drafts
`plan.md`, commits, prints the DONE summary.

- [ ] **Step 3: Verify the files were written**

```bash
ls -la feature-work/test-smoke-check/ 2>/dev/null || ls -la docs/test-smoke-check/
```

Expected: `problem.md` and `plan.md` present, no `design.md`.

- [ ] **Step 4: Clean up smoke test**

```bash
rm -rf feature-work/test-smoke-check docs/test-smoke-check 2>/dev/null
git checkout -- . 2>/dev/null || true
```

---

## Rollback

All files are local or in personal repos. No production systems are affected. To undo:

- Plugin: `claude plugins uninstall caderon-pack && claude plugins marketplace remove caderon-pack`
- Chezmoi: remove the `[".claude/plugins/caderon-pack"]` block from `.chezmoiexternal.toml`
- Repo: delete `~/Projects/personal/caderon-pack`

## Out of scope for this plan

- npm publishing (future deliverable — `package.json` is already npm-ready)
- Adding a second skill or command to the pack
- Automating the per-machine `marketplace add` + `plugins install` step via chezmoi

## Change log

- 2026-05-25: Initial draft (brent)
