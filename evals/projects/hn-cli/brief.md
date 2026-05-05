# Hacker News CLI

A small command-line tool that prints Hacker News top stories.

## Audience

- Developers who live in the terminal and want to skim HN headlines
  without opening a browser tab.
- People who already read HN regularly and want lower-friction reads
  during a focused work session.

## Pitch

```
$ hn-cli top --limit 10
1.  428  Show HN: ...                  https://news.ycombinator.com/item?id=...
2.  391  Why your build is slow        https://news.ycombinator.com/item?id=...
...
```

`hn-cli top --limit N` is the headline feature: get the top N stories
with score and link. Optional filters (`--min-score`, `--type`)
narrow the list. Optional `--format json` emits structured output for
scripts.

## Non-goals (product-level)

- Browsing comment threads. This is a "skim the headlines" tool, not
  a full reader.
- Submitting stories, commenting, voting, authentication.
- Realtime polling. Each invocation is one-shot.
- Rich TUI / panels / colors. Plain text by default; JSON for scripts.
- Caching. Each invocation hits the API (or, in eval mode, the
  recorded fixture corpus).

## Intended scope

Bones:
- Operator runs `hn-cli top --limit N`. The CLI fetches story ids and
  prints `<rank>  <score>  <title>  <url>` for each.

MVP:
- `--min-score N` filters out low-score stories.
- `--type {story,job,ask,show}` filters by HN item type.
- `--format {text,json}` switches output shape.

Final (out of MVP scope; sketched here for layered "done-enough"):
- Caching with TTL.
- Pagination beyond top 30.

## Capabilities (what the user can do)

- `fetch-top-stories` — given a `--limit`, return the top N stories.
- `filter-by-score` — given `--min-score`, drop stories below the
  threshold.
- `filter-by-type` — given `--type`, drop stories of other types.
- `format-output` — render the result as text or JSON.

## Modules (architecture)

- `api-client`: wraps the HN Firebase API. Single responsibility:
  given a request, return parsed `Story` objects. Replays from
  fixtures during eval runs.
- `cli-frontend`: parses argv, calls `api-client`, formats and prints.

## External dependencies

- HN Firebase API (`https://hacker-news.firebaseio.com`).
- For eval reproducibility, recorded into
  `evals/projects/hn-cli/fixtures/hn-api.jsonl` and replayed.

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
