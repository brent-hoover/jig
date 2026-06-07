---
title: caderon-pack — Design
type: design
status: active
owner: brent
created: 2026-05-25
updated: 2026-05-25
problem: N/A — no separate problem.md; design emerged from brainstorming session
repository: https://github.com/brent-hoover/caderon-pack
---

# caderon-pack — Design

## Summary

A personal Claude Code plugin pack (`caderon-pack`) that consolidates personal skills, commands, and hooks
into a single versioned git repository, installable on any machine. The initial content is a `start-feature`
skill that guides Claude through the problem → design → plan documentation workflow using a state machine
with approval gates. Chezmoi integration automates installation across machines as an opt-in step.

## Approach

The plugin is a standalone git repository following the Claude Code plugin spec. It has no runtime
dependencies beyond Claude Code itself. Skills are the primary component type — no MCP servers, no hooks
initially.

The `start-feature` skill is a state machine with four named phases:

- **PROBLEM**: Claude asks targeted questions, reads project CLAUDE.md for doc conventions, drafts
  `problem.md` from template, waits for approval.
- **DESIGN**: Claude proposes 2–3 approaches with trade-offs, gets a selection, drafts `design.md`,
  waits for approval. Skippable for trivial features via explicit question at PROBLEM→DESIGN transition.
- **PLAN**: Claude drafts `plan.md` with ordered steps (each sized for one PR/session, with What/Why/Verify
  fields), waits for approval.
- **DONE**: Commits all new docs, prints a summary of files created.

Claude announces the current phase at the start of each step. Each phase reads the relevant template via
`${CLAUDE_PLUGIN_ROOT}` and produces a fully populated doc — no placeholder sections left for the user.

The skill detects project-specific doc conventions by reading CLAUDE.md. For jig-style projects it writes to
`feature-work/<slug>/`. For projects with no stated convention it defaults to `docs/<slug>/`. Owner and
dates are auto-populated from git config and the system clock.

## Interfaces

**Skill trigger**: activated by the `start-feature` description when user says "start a feature", "new
feature", or invokes `/start-feature [<feature-slug>]`. If a slug is provided as argument, the skill skips
asking for it.

**Output**: three files written to disk (`problem.md`, optionally `design.md`, `plan.md`) plus a git commit
at DONE. `deferred.md` and `completed.md` are available as standalone templates outside the main pipeline:

- **completed.md** — handoff manifest written when work ships: new modules, added dependencies, changed
  interfaces, anything a future reader needs to understand what is now in the codebase as a result of this
  feature. Authoritative "what was built."
- **deferred.md** — over-engineering outlet written when planned work is intentionally skipped: what was
  not implemented and the specific reason why. Reviewed at project close to confirm nothing critical was
  silently dropped.

**Archiving workflow**: when a feature is complete and `completed.md` is written, the entire
`<doc-root>/<slug>/` directory moves to `<doc-root>/archived/<slug>/`. This keeps the doc root
reflecting only current in-progress work. The skill's DONE phase includes a reminder to archive
once implementation is finished.

**Templates**: stored as plain markdown files at
`${CLAUDE_PLUGIN_ROOT}/skills/start-feature/templates/`. The skill reads them with the Read tool at runtime.
Full set: `problem.md`, `design.md`, `plan.md`, `deferred.md`, `completed.md`.

## Data model

No persistent state. All state is in the conversation context during a session. Output is plain markdown
files written to the project filesystem.

## Alternatives considered

### Single slash command file (linear script)

A `.claude/commands/start-feature.md` in jig or globally. Simpler to write, no plugin infrastructure. But
it's per-project or requires manual placement on each machine, doesn't scale to holding additional skills
and commands over time, and can't carry template files alongside it.

### Three separate commands (`/start-problem`, `/start-design`, `/start-plan`)

One command per phase, chained manually. Simpler per-file, but loses the gated pipeline feel and requires
the user to remember to invoke the next phase. The whole point is a guided end-to-end walk.

### Chosen: plugin with a single state-machine skill

Plugin infrastructure lets the repo grow to hold future skills and commands. State machine handles the
trivial-feature branch cleanly. Template files as plugin assets keep the SKILL.md readable. Standalone repo
means it works without jig, without chezmoi, without anything except a Claude Code installation.

## Risks

- Claude Code's plugin auto-discovery must correctly load the skill from the repo path on install. Needs
  verification after initial setup.
- The CLAUDE.md detection heuristic (read project CLAUDE.md, look for doc path patterns) could misfire on
  projects with unusual conventions. Mitigation: skill falls back gracefully to `docs/<slug>/` and prints
  the path it chose so the user can correct it.
- Chezmoi `.chezmoiexternal.toml` approach clones the repo on every new machine but doesn't auto-update.
  Acceptable — `git pull` in the plugin dir is sufficient for updates.

## Out of scope

- No MCP servers, no hooks in the initial release — just the one skill.
- No spec file generation (`REQ-*.md`) — that's a separate step added manually when needed.
- No PR creation or ticket creation — doc scaffolding only.
- Chezmoi integration for others — the README will note it as optional; the plugin itself has no chezmoi
  dependency.

## Future deliverables

- **npm publishing**: Publish `caderon-pack` to npm so it can be installed with `npm install -g caderon-pack`
  instead of requiring a manual clone + local registration. Not in scope for the initial release but the repo
  structure should not prevent it (i.e., `package.json` should be present and valid from day one).

## Open questions

(none)

## Change log

- 2026-05-25: Initial draft (brent)
- 2026-05-25: Added GitHub URL, npm future deliverable, deferred.md and completed.md template types (brent)
