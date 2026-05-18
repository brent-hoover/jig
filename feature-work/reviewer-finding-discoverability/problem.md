---
title: Reviewer Finding Discoverability — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-17
updated: 2026-05-17
---

# Reviewer Finding Discoverability — Problem Statement

## Context

The multi-reviewer federation persists every comment a reviewer raises to
`.jig/store/review_comments.jsonl`, backed by `ReviewCommentsStore`
(`jig/store/review_comments.py`). Each record carries a rich payload: `ticket_id`, `cycle`, `reviewer`,
`severity` (critical / important / notable), `type`, `file`, `line`, free-form `prose`,
`suggested_diff`, `confidence`, optional `contract_uri` and `evidence`.

The operator's primary inspection surfaces are:

- The TUI, which reads ticket + thread state from `TicketStore` and `ThreadStore`.
- `jig story <ticket-id>` (`jig/cli.py:873`), which loads `comments.jsonl` (threads) and
  `tickets.jsonl` only — `build_story` is hard-wired to those two stores.

The orchestrator's only direct surface for the federation outcome is a single INFO log line per cycle:
`review federation blocked ticket X: N critical, M important`. That message strips the file, line,
prose, severity breakdown by reviewer, and suggested diffs.

## Problem

The data the operator needs to diagnose a review impasse is captured cleanly in the store but never
surfaced through any operator-facing command or view. To answer "why did review block this ticket?" the
operator has to:

1. Notice the ticket failed or stalled (no dedicated signal; see related problem docs).
2. Locate `.jig/store/review_comments.jsonl` by hand.
3. Write a `jq` query against the JSONL to filter by `ticket_id` and `severity`.
4. Read the raw JSON, including a `suggested_diff` that's not formatted for terminal viewing.

This makes the federation feel opaque from the outside: the orchestrator's log says "1 important" and
the operator has no path to see what "1 important" *was* without leaving the tools jig provides. The
session in which this work originated needed five layered `jq` invocations to recover the actual
reviewer feedback, and that was for a single ticket on a small project.

The gap matters in three concrete ways:

- **Triage** — when a ticket fails or escalates (post phase-failure-escalation work), the operator
  can't decide retry/skip/fail without seeing the underlying findings.
- **Calibration** — assessing whether reviewers are being helpful, pedantic, or wrong requires reading
  their full prose across many tickets. Without a CLI / TUI view, this happens only in ad-hoc
  spelunking.
- **Audit trail** — a successful ticket's history of review-and-fix cycles is invisible. There is no
  way to ask "what did reviewers find on ticket X, and what changed between cycles?"

## Simplest possible solution

Extend the existing inspection surfaces to read `ReviewCommentsStore`:

1. **`jig story` integration.** `build_story` already merges typed event streams from multiple stores
   into a chronological narrative. Add `ReviewCommentsStore` as one more source: each
   `ReviewerComment` becomes a story event with its cycle, reviewer, severity, file:line, prose, and
   suggested_diff. The existing `--level` filter (info / debug etc.) extends naturally to gate verbosity
   of the diff bodies.

2. **`jig ticket review-findings <ticket-id>`** (new subcommand, or a flag on `jig ticket show`). For
   scripted triage and CI integration, a focused JSON / table output of "current cycle's blocking
   findings" without the surrounding story noise. Useful for an operator-escalation prompt body
   (see phase-failure-escalation).

3. **TUI surfacing.** When a ticket is selected in the TUI, show a "Review Findings" pane (collapsed by
   default) that lists severity-bucketed comments for the current cycle. Click-through to expand the
   prose + suggested diff. Reuses existing pane layout.

Items 1 and 2 are CLI-side and small. Item 3 is TUI-side and bigger; can land in a second pass.

The store, schema, and access methods all exist. This is a presentation gap, not a data gap.

## Complications considered

- **Scale**: Comments per ticket per cycle are bounded by reviewer count × file-count touched. Default
  federation has ~3 reviewers; ticket diffs typically touch <20 files. Even a worst-case ticket
  produces hundreds of comments across cycles, not thousands. No special pagination needed for `jig
  story`; the TUI may need scroll affordances for a comment-heavy ticket, but that's UI work, not
  fundamental.
- **Concurrency**: N/A — the store is read-only from the inspection surfaces.
- **Failure modes**: Missing `review_comments.jsonl` (older projects without federation runs) — render
  nothing, don't crash. Malformed comments (schema drift) — fail loud on parse, don't silently drop;
  schema is owned in-tree and changes should bump explicit versioning.
- **Cross-cutting policies**: N/A — comments are project-internal data.
- **Verbosity vs. signal**: a long-running ticket may have many cycles of comments. Default `jig story`
  output should show severities + one-line `prose` head; full prose + diff behind `--verbose` or a
  per-event toggle.

## Constraints

- Must compose with the existing story-merging pipeline in `jig.story.build_story` — that pipeline
  already accepts multiple `StorySource`s and merges by timestamp. Adding one more source is the
  intended extension shape.
- The ordering key for review-comment events in the merged story must be deterministic and
  monotonic. Either verify the Collection-assigned id is a usable monotonic timestamp before
  relying on it as the sort key, or use `(cycle, reviewer, insertion-order)` as the primary
  ordering from the start. Do not ship a design that assumes id-is-timestamp without confirming —
  silent merge-order drift between stores is the failure mode this hardens against.
- The CLI is the priority; the TUI surface can lag and ship in a follow-up without blocking the
  user-visible benefit.
- Output must be readable in a terminal without ANSI tricks; use the existing formatting conventions
  in `jig story`.

## Requirements

- `jig story <ticket-id>` includes review findings in its merged event stream, scoped by ticket and
  filterable by severity / cycle.
- A scripted-friendly query path (subcommand or flag) returns the blocking findings for a given
  ticket-cycle as structured JSON, suitable for embedding in operator-escalation prompts and CI
  output.
- Display includes: cycle, reviewer name, severity, file:line, prose, suggested_diff. Severity is
  visually distinct (color or prefix) so blocking findings (critical / important) stand out.
- `suggested_diff` renders as a readable unified diff, not raw JSON-escaped string.
- TUI gains a finding-list pane in a follow-up phase; not blocking for the CLI work.

## Non-goals

- New reviewer types, severity calibration, or any change to the federation itself.
- Web UI / external dashboards — terminal-first.
- Cross-ticket aggregation views as part of *this* work. "Show me all critical findings across the
  project" is a common triage need and is a likely follow-up — flagged as note-for-later, not a
  permanent exclusion. Out of scope here because it requires its own surface design (a separate
  command, formatting decisions, filter UX) on top of the per-ticket primitives this work adds.
- Filtering by author / committer — comments are reviewer-tagged, not author-tagged, and the operator
  selects by ticket.
- Migrating reviewer comments into `ThreadStore` (i.e. consolidating stores). The two stores have
  different schemas and queries; merging them is bigger than this work needs.

## Success criteria

- `jig story <ticket-id>` shows the reviewer findings inline with the rest of the ticket's narrative,
  in chronological order, with severity-prefixed lines.
- Diagnosing the `240db21f`-style scenario (a ticket failing because of repeated reviewer findings
  in test files) takes one command and zero JSON wrangling.
- The CLI output is consumable as text for inclusion in escalation prompts (phase-failure-escalation
  uses it).
- The schema-aware JSON subcommand returns the same payload `ReviewCommentsStore.for_ticket` produces,
  ready for further tooling.

## Open questions

- [ ] Add findings as a new event-type in `build_story`'s merged stream, or expose them as a separate
      block printed after the main story? Inline is more useful for narrative reading; separate is
      easier to grep. Defaulting to inline with a `--review-only` flag for the focused view feels
      right but worth confirming.
- [ ] Subcommand naming — `jig ticket review-findings`, `jig ticket review`, `jig review` (top-level)?
      Top-level reads well but conflicts with the workflow phase name. `jig ticket review` is the
      cleanest fit if the noun-verb ordering matches other ticket subcommands.
- [ ] How does the TUI render `suggested_diff`? A monospace pane with diff highlighting is the
      obvious target. Reuse any existing diff-rendering helpers if present; otherwise this is the
      first place we need one and can build it generically.
- [ ] Should the rendered story also surface `evidence` arrays (snippets the reviewer cited)? Probably
      yes when present, but they're optional in the schema; render gracefully when absent.

## Change log

- 2026-05-17: Initial draft (brent)
- 2026-05-17: Address review feedback. Harden timestamp/ordering note from "verify" to a constraint
  (do not ship a design that assumes Collection id == timestamp without confirming; use
  `(cycle, reviewer, insertion-order)` if not). Reframe cross-ticket aggregation as note-for-later
  follow-up rather than flat non-goal.
