# todo_cli — task notes

## What this task is for

Tests whether the model can build a small, multi-subcommand CLI with file-backed persistence in one shot. The
problem has enough surface area (three commands, output formatting, error paths, persistence) that prompt-style
differences plausibly matter, but is small enough that a competent solution fits in ~60 lines.

## What the hidden tests cover

- `add` → `list` happy path; output format `"<n>. <text>"`.
- Multiple `add`s preserve insertion order; numbering is 1-based.
- `done <n>` changes the open-item marker to a `[x]` prefix.
- `done` with an out-of-range index fails (non-zero exit code).
- An empty `list` prints nothing.
- State persists across separate process invocations.

## What this task discriminates between prompt styles

- Will the model invent its own output format if not given one? The strict `"<n>. <text>" / "<n>. [x] <text>"`
  format is the easiest test bar to clear with the spec in hand, and the easiest to miss without it.
- Will it remember error handling for `done` with a bad index? Easy to elide.
- Will it correctly persist data with no third-party deps?

## Authoring rule

`prompts/yaml_spec.md` and `prompts/prose_spec.md` must encode the **same** requirements in different forms. When
editing one, diff against the other and confirm equivalence. The eval result is meaningless if the two prompts ask
for different things.
