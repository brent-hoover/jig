# Dogfood runbook — end-to-end app test

How to take `jig` from "cloned repo" to "a real agent ran a ticket through the
default workflow" on a fresh scratch project. This is what we run when we want
to find out what's actually broken before shipping anything bigger.

**Scope.** This is a real-agent run: Claude Code processes spawn, the API
bills, files get written. Use a throwaway directory. Use the mock-agent smoke
(`scripts/dogfood_smoke.py`) first if you only care about the orchestrator
loop — no API spend, finishes in a second.

---

## 0. Prerequisites (one-time)

- `CLAUDE_CODE_OAUTH_TOKEN` exported in your shell. If not:
  ```bash
  claude setup-token       # writes the token to its default location
  source ~/.secrets.env    # or however you hydrate it
  echo "len=${#CLAUDE_CODE_OAUTH_TOKEN}"  # sanity-check
  ```
- `uv` ≥ 0.4.x (`uv --version`).
- `bun` ≥ 1.3 for the TUI (`bun --version`).
- `jig` installed as an editable uv tool so `jig` on `$PATH` tracks your
  working tree:
  ```bash
  uv tool install --editable ~/Projects/personal/jig
  # verify:
  which jig                                   # ~/.local/bin/jig
  uv tool list | grep jig                     # jig v0.1.0
  ```
  After this, `jig ...` works from any directory and always runs the code at
  `~/Projects/personal/jig`. Re-run the install only if you change
  `pyproject.toml` (new deps, new console script).
- Clean working tree on the `jig` repo. Current develop or a feature branch —
  whatever you want to dogfood.

## 1. Sanity-check the jig install

Everything the user will hit should pass before you boot against a real
project.

```bash
cd ~/Projects/personal/jig
uv sync                                 # refresh the dev env for pytest
uv run pytest -q                        # full suite — must be green
uv run python scripts/dogfood_smoke.py  # mock-agent E2E — must print "PASS"
jig --help                              # editable tool install is live
```

Expected: tests green, smoke prints `[smoke] PASS`, `jig --help` lists
`build / hooks / init / reset / start / story / sync / ticket / validate`.

If any of these fail, **stop** — a real-agent run will just amplify whatever's
already broken.

## 2. Create the scratch project

`jig`'s `--path` defaults to `.`, so every step below just runs from the
scratch directory.

```bash
rm -rf /tmp/jig-dogfood && mkdir /tmp/jig-dogfood
cd /tmp/jig-dogfood
git init -q
git commit --allow-empty -qm "init"

jig init --template python --no-input
```

`jig init` should report:
- `Initialized Jig in /tmp/jig-dogfood/.jig`
- `Applied template: python`
- `Installed git hooks` (unless you pass `--no-hooks`)
- `Initial commit created`

Verify the catalog before you start the daemon:

```bash
jig validate
```

Expected on success: exit 0 and normal output such as `Catalog OK.`. The CLI
may also print `[WARN] ...` advisories; treat those as non-fatal unless the
command exits non-zero or prints a real validation error. Fix any actual
validation errors before continuing.

## 3. Write a project brief (PO step)

`jig init` scaffolds `.jig/spec/project.md` with the doc-02 section headers
already in place — open it and fill in the one-paragraph premise plus whatever
you have under each state category.

```bash
$EDITOR /tmp/jig-dogfood/.jig/spec/project.md
git -C /tmp/jig-dogfood add .jig/spec/project.md
git -C /tmp/jig-dogfood commit -qm "po: initial project brief"
```

Format reminders (full reference: `docs/02-project-spec.md` §"Human format
example"):
- `# <project name>` + one paragraph
- `## Built` / `## Planned (committed)` / `## Planned (not yet committed)` /
  `## Backlog` / `## Non-goals`
- Elaborated capabilities → level-3 headers + prose
- One-liners → bullets

When you create the first ticket, **paste the relevant capability section
into the ticket description** — the spec agent doesn't auto-pull from
`project.md` yet.

Keep the scope *tiny*: one capability, one or two functions worth of code.
First real runs blow up in places you don't expect; a big ticket wastes a lot
of tokens finding out.

## 4. Start the orchestrator

Two terminals from here on.

**Terminal A — orchestrator:**

```bash
cd /tmp/jig-dogfood
jig start --no-docker
```

Expected:
```
Logging to /tmp/jig-dogfood/.jig/logs/jig-<timestamp>.log
WebSocket server listening on ws://127.0.0.1:9100
jig.orchestrator: no ready tickets in queue
Orchestrator started. Press Ctrl-C to stop.
```

If port 9100 is already in use, a previous run didn't release it — wait ~5s or
`lsof -iTCP:9100 | tail -1` + `kill <pid>`.

`--no-docker` runs agents directly on the host. Drop the flag if you want the
sandboxed Docker path (requires `jig build` first and a working Docker
daemon; out of scope for a first dogfood).

## 5. Launch the TUI

**Terminal B — TUI:**

```bash
cd /tmp/jig-dogfood
jig daemon start    # if you didn't start one in Terminal A
jig                 # launches the Textual TUI and connects to the daemon
```

For a sandboxed run (host isolation + bwrap per agent) use `jig daemon start --docker`. Requires `jig build` first and a working Docker daemon. `jig daemon start --no-docker` forces host mode even when Docker is available.

You should see:
- four tabs at the top: `Now · Tickets · Spec · Events`
- footer showing `daemon: connected` in green
- the Now scrollback with `welcome to jig — try /help`

Press `?` for the in-app key reference. `q` exits the TUI without stopping
the daemon — the orchestrator keeps running in the background. Stop the
daemon explicitly with `jig daemon stop` when done.

If the footer shows `daemon: reconnecting` (yellow) or `disconnected`
(red), the daemon failed to start or got killed — `jig daemon status`
to inspect; restart with `jig daemon start`.

## 6. Create the first ticket

From the TUI: press `n`. Fill the modal:
- **Work type**: `feature` for a new capability, `bugfix` for regression work.
- **Size**: `s` or `xs`. First run: keep it tiny.
- **Title**: one-line summary.
- **Description**: paste the relevant section of your `project.md` brief.
  Include explicit acceptance criteria — the spec agent is less likely to
  ping you with clarifying questions when the description is concrete.

Submit. The TUI should:
1. Ack the `create_ticket` reply and add the ticket to the list.
2. Emit a `ticket_created` event (you'll see it duplicate in the live feed —
   known harmless noise from the bus + dispatch-loop double-publish).
3. Flip to `in_progress` via `ticket_updated`.
4. Fire `phase_started` with `phase_name="spec"`.

**CLI alternative.** If you'd rather script the seed (or you're dogfooding
headlessly), `jig ticket create` talks to the same WS endpoint:

```bash
jig ticket create \
  --title "add the thing" \
  --work-type feature --size s \
  --description-file /tmp/jig-dogfood/.jig/spec/project.md
# prints the ticket_id to stdout on success
```

Exits non-zero with a clean message if the orchestrator isn't running or
rejects the ticket — no traceback. Pipe the ID into whatever you want to
watch next.

## 7. Watch the phases run

Default workflow (`jig/defaults/workflows/default.yaml`):
```
spec → test → implement → review → validate → document
```

Each phase spawns a fresh Claude Code process under `jig/{ticket_id}` as its
worktree. You'll see:
- `phase_started` / `phase_complete` pairs in the event log
- `comment_posted` entries as the agents post handoffs, questions, decisions
- `commit_recorded` entries as `commit_progress` fires
- status flips to `needs_info` if the agent asks a question

**If it lands on `needs_info`:** the operator answers via the Now tab.
The active question shows up inline as a Panel and Now switches to
"answering mode" — the next text you submit is sent back as the answer
via the daemon's prompt round-trip protocol. The `answer_questions`
path flips the ticket back to `in_progress` and the phase resumes. To see what
the agent actually asked (with surrounding context), run:

```bash
jig story <ticket-id>            # merged thread + log narrative
jig story <ticket-id> --json     # one event per line for grep/jq
```

**If a phase fails:** the ticket goes to `failed`. The worktree is preserved
at `/tmp/jig-dogfood/jig/<ticket-id>/`. Reach for `jig story` first — it
interleaves thread entries (handoffs, questions, decisions, system events)
with the matching log lines, scoped to that one ticket, sorted by time:

```bash
jig story <ticket-id>
jig story <ticket-id> --since 2026-04-25T14:00:00   # narrow to the failing run
jig story <ticket-id> --include-children            # if it has subtickets
jig story <ticket-id> --level DEBUG                 # include DEBUG log lines
```

Drop into `/tmp/jig-dogfood/.jig/logs/jig-<timestamp>.jsonl` only if the
story is missing context (an unticketed crash, daemon-level errors, etc).

**If everything works:** after `document` finishes, the orchestrator merges
`jig/<ticket-id>` into your default branch, emits `ticket_completed`, and the
TUI flips it to `resolved`. Check the scratch repo:
```bash
cd /tmp/jig-dogfood && git log --oneline | head -20
```
You should see a commit chain from each phase plus the merge.

## 8. Fail modes to watch for

Things that have bitten us before. When one of these shows up, capture it in
the "friction" list — that's the actual output of a dogfood run.

- **Merge conflict during ticket close.** Ticket flips to `merge_conflict`;
  worktree and branch preserved. Resolve by hand (`cd jig/<id>`, merge
  manually) or `jig validate --ticket-id <id>` to clean up.
- **WS port not releasing on Ctrl-C.** Known race in `ws_server.stop()`. Wait
  5s or kill the leftover listener explicitly.
- **Auto-commit failures logged but non-fatal.** If a phase produces no
  diff, `commit_worktree` fails silently into the log. Fine, but worth
  confirming it doesn't cascade.
- **Duplicate `ticket_created` events.** Cosmetic; the TUI reducer is
  idempotent. Flag if the count goes >2 — that would mean a new publish
  path appeared.
- **Phase blocked by thread.** Something posted a blocking Objection or a
  Handoff that nobody accepted. `phase_blocked_by_thread` lands on the bus;
  TUI logs it. Human action required — accept the handoff or resolve the
  objection via the thread tools. `jig story <ticket-id>` shows exactly
  which thread entry is blocking and who posted it.

## 9. Reset between runs

```bash
# Stop orchestrator (Ctrl-C in Terminal A)
# Stop TUI (q in Terminal B)

cd /tmp/jig-dogfood && jig reset --yes
# or blow it away entirely:
rm -rf /tmp/jig-dogfood
```

`jig reset` keeps `.jig/` + project template, drops tickets/threads/worktrees.
Faster iteration than re-init.

## 10. What you're actually looking for

A dogfood run isn't "did it work." It's:

1. **What broke?** Every traceback, every stuck ticket, every confusing TUI
   state goes into `docs/friction.md` (create it if needed) with enough
   context to reproduce.
2. **What was annoying?** Every spot where you had to read source to know
   what to do next. Those are UX bugs worth filing.
3. **What did the agents do that you didn't expect?** Good or bad. Log it.
4. **How long did each phase take?** Rough eyeballing is fine. Helps spot
   prompt regressions.

The point is to generate the next slate of tickets. First pass won't be
clean. That's the feature.
