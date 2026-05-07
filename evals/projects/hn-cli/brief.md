# Hacker News CLI

A small command-line tool that prints Hacker News top stories.

## Built

## Planned (committed)

### Fetch top stories {#fetch-top-stories}

`hn-cli top --limit N` fetches the top N Hacker News stories from the HN API and prints them as a ranked list with score, title, and URL.

**User story:**
As a developer in the terminal, I want to fetch the top HN stories quickly so I can skim headlines without opening a browser.

**Behaviors:**
- {#run-top-cmd} `hn-cli top --limit N` prints N stories ranked by HN score, one per line.

**Acceptance criteria:**
- [run-top-cmd] Exit code is 0 on success.
- [run-top-cmd] `--limit N` is the size of the candidate pool fetched from the HN API,
  not a guarantee of output row count. Filters (`--min-score`, `--type`) are applied
  AFTER the limit-N fetch and MAY reduce the output to fewer than N rows. The tool
  MUST NOT over-fetch or paginate to backfill filtered rows — operators wanting more
  chances after filtering raise `--limit`.
- [run-top-cmd] Output contains AT MOST N lines; fewer is correct when the API
  returned fewer than N stories OR filters rejected some candidates.
- [run-top-cmd] Each line matches the format `<rank>.  <score>  <title>  <url>`
  (e.g. `1.  428  Show HN: ...  https://...`).
- [run-top-cmd] Ranks in the final output are renumbered 1..K contiguously, where K
  is the post-filter row count (NOT the original HN rank).
- [run-top-cmd] In eval mode, story IDs and titles match the fixture corpus
  deterministically (tracer: `hn-cli top --limit 3`).

---

### Filter by score {#filter-by-score}

`--min-score N` drops any story whose score falls below N, leaving only high-signal items.

**User story:**
As a developer, I want to filter out low-score stories so I only see popular content.

**Behaviors:**
- {#min-score-flag} `--min-score N` removes stories with score < N from the output.

**Acceptance criteria:**
- [min-score-flag] No story with score below N appears in the output.
- [min-score-flag] Stories with score >= N are retained and not removed.

---

### Filter by type {#filter-by-type}

`--type {story,job,ask,show}` restricts results to a single HN item type.

**User story:**
As a developer, I want to see only "Show HN" posts (or only jobs, etc.) so I can focus on relevant content.

**Behaviors:**
- {#type-flag} `--type <type>` removes items that don't match the given HN item type from the output.

**Acceptance criteria:**
- [type-flag] Only items of the specified type appear in output.
- [type-flag] Items of other types are absent from output.

---

### Format output {#format-output}

The default output is human-readable plain text; `--format json` switches to structured JSON for scripting.

**User story:**
As a developer, I want JSON output so I can pipe `hn-cli` into other tools.

**Behaviors:**
- {#text-format} Default output (no `--format` flag) is plain text, one story per line.
- {#json-format} `--format json` emits a JSON array of story objects.

**Acceptance criteria:**
- [text-format] Default output lines match `^\d+\.\s+\d+\s+\S.*\s+https?://\S+$`.
- [json-format] `--format json` output is valid JSON.
- [json-format] Each JSON object contains rank, score, title, and url fields.

## Planned (not yet committed)

- {#caching} Caching with TTL — cache API responses to avoid redundant fetches within a short window
- {#pagination} Pagination beyond top 30 — fetch more than the default HN top-30 window

## Backlog

## Archived

## Non-goals

- {#no-comments} Browse comment threads — hn-cli is a "skim the headlines" tool, not a full reader
- {#no-auth} Submit stories, comment, vote, or authenticate — read-only tool
- {#no-realtime} Realtime polling — each invocation is one-shot, not a live feed
- {#no-rich-tui} Rich TUI with panels or colors — plain text by default; JSON for scripts
- {#no-filter-backfill} Over-fetching or paginating the HN API to backfill filtered-out rows — `--limit N` is the candidate pool size, not a guaranteed output row count.

## Tracer

```
hn-cli top --limit 3
```

Pass conditions:

- Exit code 0.
- Three lines in stdout, each matching
  `^\d+\.\s+\d+\s+\S.*\s+https?://\S+$`.
- Story ids and titles match the fixture corpus deterministically.

Tracer is exercised by the bones bundle; later tickets that touch
either module must keep it green.

## Synthetic-operator notes

The synthetic operator's answers shape the project. For
reproducibility, pin these answers per eval run:

- During L1 discovery, accept one persona ("terminal-friendly
  developer") and one journey ("read top stories, optionally
  filtered").
- During L3 elaboration, accept the four capabilities listed above.
- During SA, accept the two-module split.
- During PM planning, accept bones-then-MVP layering.
- During reviewer-question prompts, prefer "no" / "skip" answers
  unless the question is structurally necessary — keep the eval
  short.

## Pass/fail summary

- Tracer green at end of bones run.
- All bones tickets in `resolved` state.
- No `provisioning_failed` / `auto_commit_failed` /
  `reviewer_spawn_failed` SystemEvents.
- Critical reviewer comments == 0 at handoff.
- Total cost / wall-clock within budget set by Phase 0.4 thresholds.
