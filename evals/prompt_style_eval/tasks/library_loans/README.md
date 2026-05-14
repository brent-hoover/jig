# library_loans — task notes

## What this task is for

A small library / loans tracker built across three files (`app.py`, `client.py`, `storage.py`). The task is
chosen specifically to put pressure on two failure modes that we've observed Claude tends toward in real
multi-piece work:

1. **Completeness**: enumerated requirements that aren't strictly needed for the happy path are easy to drop.
   The behavioral spec calls out things like "due_date defaults to 14 days", "borrowed_at is auto-set",
   "returning a returned loan is an error", "history is oldest first", "deleting a book with an open loan
   is an error" — none of which a basic CRUD walkthrough would hit. The hidden tests have one assertion per
   stated AC, so dropped requirements show up as test failures.

2. **Coherence**: three modules have to agree on field names, IDs, and shapes. If `app.py` calls a field
   `borrowed_at` but `storage.py` writes it as `borrow_time`, the round-trip persistence test fails because
   the server can't read its own data back. If `client.py` POSTs to `/loans` but `app.py` registered the
   route as `/borrow`, every borrow-related test fails. Tests don't peek directly into storage — the
   coherence proof is end-to-end behavior.

## What the hidden tests cover (behaviorally)

Roughly 25 tests, one per stated AC plus a round-trip persistence test:

- Books: add → appears in list, newly added is available, list-with-filter, get existing, get missing
  raises, delete-without-loan, delete-with-loan raises, delete-missing raises.
- Loans: borrow creates loan + flips availability, borrow-already-borrowed raises, borrow-missing-book
  raises, default due_date is +14d, borrowed_at auto-set, explicit due_date honored.
- Return: makes book available again, returned_at auto-set, missing raises, already-returned raises.
- Loan listing: default contains everything, filter open, filter returned.
- History: oldest-first ordering, empty for never-borrowed, missing-book raises.
- Persistence: books + loans + availability all survive a fresh module import.

Tests are status-code-agnostic and field-format-agnostic where possible (e.g. ISO date strings are parsed
via `datetime.fromisoformat`, not compared as exact strings). They check behaviors.

## Prompt asymmetry is intentional

- `yaml_spec.md` is in jig's project-spec format with explicit, testable acceptance criteria per behavior.
- `prose_spec.md` is in user-story voice — a single librarian's perspective written across the four
  capability areas, without enumerated ACs.

Both prompts share the same project-context prefix: implementation constraints, file layout, and the Python
client API (signatures + exceptions). The wire protocol and storage format are the candidate's design in
both cases.

## What this task discriminates between prompt styles

If our completeness hypothesis holds, the prose version should drop more enumerated requirements than the
YAML version, even with the same per-capability behavioral description. Specifically, prose is more likely
to elide:

- The 14-day default on `due_date`
- The "already-returned is an error" path
- The "delete with open loan is an error" path
- The history ordering constraint
- The filter combinations (especially `available=False` and `returned=True`)

If our coherence hypothesis holds, both prompts will sometimes produce app/storage divergences that fail
the persistence round-trip — but the rate may differ.

## Implementation-choice observation (not via tests)

URLs, status codes, request/response shapes, storage file format, ID generation strategy, validation
strictness — all observable from the persisted `extracted_files` per run. Surfaced by the reporter, not
the tests.
