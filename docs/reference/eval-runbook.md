# Eval runbook

How to run jig against a baked brief, capture a run manifest, and
compare runs over time.

## Layout

```
evals/
  projects/                  # input fixtures (one per eval project)
    hn-cli/
      brief.md               # the pre-baked PO output
      tracer.sh              # passes/fails the built artifact
      fixtures/              # any data the tracer needs
    recipe-browser/
      brief.md
      tracer.py
      content/
  runs/                      # output manifests
    hn-cli/
      <run-id>/
        manifest.yaml        # `jig eval collect` writes this
```

`evals/projects/<id>/` is the input. `evals/runs/<id>/<run-id>/` is the
output. The harness never writes to `projects/`; the brief + tracer
are checked-in fixtures.

## Workflows

### A. Unattended run (the eval harness path)

Drives jig from a baked brief through spec → architecture → scaffold
without any operator prompts. This is what you want for batch runs.

```bash
# from anywhere
jig init evals/runs/hn-cli-001 \
  --brief evals/projects/hn-cli/brief.md \
  --auto
```

What `--brief` does:
- copies the file into the new project at `docs/brief.md`
- pre-seeds the `brief` ticket as RESOLVED with a Handoff +
  `brief_approved` SystemEvent
- the resume classifier lands directly on `SPEC_GENERATION`,
  skipping the PO conversation

What `--auto` does:
- swaps the prompt handler so brief approval = Y, branch = SA,
  scaffold confirm = Y
- free-text agent questions raise loudly (an unattended run that
  needs an answer is a misconfiguration, not something to silently
  default)

The flags compose. `--brief` alone gives you a baked brief but still
prompts at brief approval / branch choice / scaffold confirm — useful
when you want to eyeball the spec before committing.

### B. Same flow, from inside the TUI

```
/init runs/hn-cli-001 --brief=projects/hn-cli/brief.md --auto
```

- relative paths resolve against the TUI's `project_path` (the
  directory `jig` was launched from). Use `--brief=...` form;
  `--brief PATH` (space) also works.
- `--auto` makes the TUI use `AutoPromptHandler` instead of round-
  tripping prompts to the Composer
- `/init --brief=brief.md` (no name) initializes in-place — uses
  the current `project_path`'s basename as the project name

### C. Manual run (no baked brief)

Standard `jig init` — the PO conversation runs, you author the brief
interactively, prompts gate every transition.

```bash
jig init mydir
```

Useful when authoring a new eval fixture: run the PO conversation,
then check the resulting `docs/brief.md` into `evals/projects/<id>/`.

## Collecting a run

After a run completes (project is at scaffold-applied), capture a
manifest:

```bash
jig eval collect evals/runs/hn-cli-001 \
  --project-id hn-cli \
  --label "post-cascade-fix" \
  --tracer-cmd "bash ../../projects/hn-cli/tracer.sh"
```

- `PROJECT_PATH` (positional) is the jig-managed project to inspect
- `--project-id` groups runs together (matches the directory name in
  `evals/projects/`)
- `--label` is a human-readable tag — used as the `--before` /
  `--after` argument to `eval compare` later
- `--tracer-cmd` is run from inside `PROJECT_PATH`. Use a relative
  path back up to the project's tracer if you keep tracers in
  `evals/projects/`, as shown above

Output:
```
run_id:   3a9f2c7e
label:    post-cascade-fix
manifest: /Users/.../evals/runs/hn-cli/3a9f2c7e/manifest.yaml
tickets:  {'closed': 12, 'resolved': 3}
cost_usd: 1.4231
spawns:   18  fix_cycles: 2
tracer:   PASS
```

A run that didn't reach scaffold-applied still produces a manifest —
look at `ticket_status_counts` and `system_event_counts` to see where
it stalled.

## Listing runs

```bash
jig eval list hn-cli
```

```
run_id     label                date         tickets                                  tracer
----------------------------------------------------------------------------------------------
3a9f2c7e   post-cascade-fix     2026-05-06   {'closed': 12, 'resolved': 3}            PASS
b1d448aa   baseline             2026-05-04   {'closed': 9, 'resolved': 6}             FAIL
```

## Comparing runs

```bash
jig eval compare hn-cli \
  --before baseline \
  --after  post-cascade-fix
```

Prints a markdown delta table — ticket counts, cost, spawn count,
fix-cycle count, reviewer findings, tracer pass/fail. `--before` and
`--after` accept either a label string or an exact run-id.

## Adding a new eval project

1. Create `evals/projects/<id>/brief.md`. Use the same shape the PO
   would author — capability headings with `{#kebab-id}` anchors,
   behaviors, acceptance criteria.

2. Add a tracer at `evals/projects/<id>/tracer.{sh,py}`. The tracer
   exits 0 on pass and non-zero on fail. Stdout is captured into the
   manifest's `tracer.stdout` field.

3. Drop any fixture data the tracer needs into the project directory
   (`fixtures/`, `content/`, etc.).

4. Smoke it:
   ```bash
   jig init /tmp/scratch-<id> --brief evals/projects/<id>/brief.md --auto
   jig eval collect /tmp/scratch-<id> \
     --project-id <id> \
     --label "smoke" \
     --tracer-cmd "bash $(pwd)/evals/projects/<id>/tracer.sh"
   ```

## Where things go wrong

- **`--brief PATH` says "file not found"** — the path is resolved
  relative to the project_path the TUI/CLI is rooted at. Use absolute
  paths when in doubt.
- **`AutoPromptHandler` raises on a free-text question** — an agent
  asked the operator something. The brief is incomplete, or
  spec-generator hit a gap that requires more product info. Either
  fix the brief or run interactively without `--auto` to see what's
  being asked.
- **Tracer SKIPs because the binary isn't on PATH** — the tracer is
  designed to be a no-op when the project hasn't built the artifact
  yet (e.g. `command -v hn-cli &>/dev/null` check). The manifest will
  record `tracer: PASS` with exit_code=0; treat that as "tracer not
  exercised", not a real pass.
- **Manifest empty / fields missing** — `eval collect` reads from the
  project's `.jig/store/`. If the run crashed before any ticket
  events, the stores are empty and the manifest will be sparse. Look
  at `.jig/logs/` for the daemon log.
