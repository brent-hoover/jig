# todo_api — task notes

## What this task is for

A meaningfully larger problem than `todo_cli`: five resource operations over HTTP, request/response validation,
filtering, error paths (404 for missing resources, 4xx for bad input), and file-backed persistence. ~150 LoC of
solution. Enough surface area that prompt-style detail may produce visible signal — different specs leave
different decisions to the developer, and some of those decisions will be observable in test outcomes.

## What the hidden tests cover (behaviorally)

The fixture re-imports the candidate's `app` module per test with `cwd` set to a fresh `tmp_path`, so each test
starts with empty storage. Then 14 tests, behaviorally focused:

- Creating an item returns success and an identifier.
- The created item appears in the listing.
- Fetching by identifier works; fetching a missing identifier is a client error (4xx).
- Multiple created items are all preserved.
- Marking an item complete persists across reads.
- Updating an item's text persists.
- Updating a missing item is a 4xx.
- Filtering the list by open / done status works.
- Deleting an item succeeds and the item disappears from listings and subsequent reads.
- Deleting a missing item is a 4xx.
- State persists across separate app imports (file-backed, not in-memory).

Tests check status-code *class* (`// 100 == 2` for success, `4` for client error) rather than exact codes —
201 vs 200, 204 vs 200, 404 vs 422 are all conventions the developer chooses.

## Prompt asymmetry is intentional

Same principle as `todo_cli`:

- `yaml_spec.md` is in jig's project-spec format with explicit testable acceptance criteria per behavior.
- `prose_spec.md` is in user-story voice — what the API is for, who uses it, what they want.

Neither prompt prescribes HTTP method choices, status codes, URL shapes, or response body schemas. Those are
the developer's call.

The eval question: does jig's structured spec produce more reliable HTTP behavior than user-story prose, on a
problem big enough that "reliable behavior" is a non-trivial bar?

## What this task discriminates between prompt styles

- URL design: `/todos/{id}` vs `/todo/{id}` vs `/items/{id}` — does either form guide the model toward a more
  conventional shape?
- Error handling: 404 for missing resource is the convention but models sometimes do 422 or 500. The YAML's
  "client-error response" hint is permissive but does point in the right direction.
- Filtering: query string (`?status=open`) is conventional but other shapes (`/todos/open`, body-with-GET) are
  possible. Does the YAML's "filtered to only those that are open or only those that are completed" make this
  obvious?
- Persistence shape: a single JSON file is the simplest path; will the model pick that or invent something
  more elaborate?

## Implementation-choice observation (not via tests)

URL shapes, status code choices, persistence file format, validation strictness — all observable from the
persisted `extracted_code`. Surfaced by the reporter, not the tests.
