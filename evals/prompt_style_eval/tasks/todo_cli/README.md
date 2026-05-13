# todo_cli — task notes

## What this task is for

Tests whether the model can build a small, multi-subcommand CLI with file-backed persistence in one shot. The
problem has enough surface area (three commands, output formatting, error paths, persistence) that prompt-style
differences plausibly matter, but is small enough that a competent solution fits in ~60 lines.

## What the hidden tests cover (behaviorally)

The tests check *behaviors* — not specific output strings or exit codes. They tolerate any reasonable choice the
model makes for HOW:

- Listing an empty store produces no output.
- An added item appears in subsequent listings.
- Multiple added items appear in insertion order.
- Each item gets its own line.
- After marking an item complete, its line in the listing *changes*; other items' lines do not. The model's
  choice of how to mark completion (`[x]`, `✓`, `(done)`, strikethrough, …) is irrelevant — only that open
  and completed items are visually distinguishable.
- `done` on a position that doesn't exist signals an error: either a non-zero exit code OR output on stderr.
- State survives across separate process invocations.

The tests deliberately do **not** check exact format strings. A spec doesn't tell developers what character to
use as a bullet; the tests shouldn't either.

## Prompt asymmetry is intentional

The two prompt variants are **not content-equivalent**, and that's the point:

- `yaml_spec.md` is in jig's project-spec format (`name`, `summary`, `capabilities` with `user_story` +
  `behaviors` + `acceptance_criteria`, `non_goals`). That format makes you write down *behaviors* — testable
  things the system does for the user.
- `prose_spec.md` is in user-story voice: high-level outcomes a PM might write, without explicit ACs.

Neither prompt should prescribe HOW the code is written, HOW the CLI is invoked, what exit codes to use, or how
to format output. Real specs describe *behavior*, not implementation choices.

The eval question is: **does jig's structured spec — by forcing you to articulate behaviors as testable ACs —
produce code that satisfies those behaviors more reliably than the looser user-story prose people would
naturally write?**

When editing a prompt, preserve its native voice. The YAML stays jig-schema-shaped with explicit behavioral
ACs; the prose stays story-shaped. Resist the urge to add exit codes, format strings, or interface details to
either — those belong in the developer's head, informed by Python convention, not in the spec.

## What this task discriminates between prompt styles

- With explicit behavioral ACs (YAML), does the model converge to passing behaviors more often than under
  loose user-story prose?
- Does either form lead the model to forget the error path on `done <bad_index>`?
- How much variance does each form induce in the model's interface choices (subcommand names, output format)?

## Implementation-choice observation (not via tests)

Things like *which subcommand names the model picked*, *what output format it used*, *what completion marker
it chose*, *what persistence file format it chose* are interesting signals but not pass/fail concerns. They're
recoverable from the persisted run records (`transcript` + `extracted_code`) and are surfaced by the reporter
(plan step 12) as descriptive statistics per prompt style, not as test results. Adding test assertions for
these would conflate "did the model honor the spec" with "did the model happen to pick our preferred
convention."
