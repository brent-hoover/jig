---
title: Automated Integration Testing — Problem Statement
type: problem
status: active
owner: Brent Hoover
created: 2026-06-07
updated: 2026-06-10
---

# Automated Integration Testing — Problem Statement

## Context

Jig is a multi-agent orchestrator. After any significant change, the primary way to verify the system
hasn't regressed is to run it end-to-end against a reference project — specifically hn-cli, a small
CLI tool whose expected output is fully specified and mechanically verifiable via a tracer script.

Several pieces are already in place: `AutoPromptHandler` answers all init-time prompts without
operator input; `jig init --auto --brief <file>` drives the PO→spec→SA→scaffold pipeline
unattended; `evals/projects/hn-cli/brief.md` is a checked-in fixture; `jig eval collect` archives
a run manifest after the fact; `jig.evals.watcher` (`StallDetector` + `run.py`) connects via
WebSocket, detects stalls across multiple signals (bus silence, heartbeat gap, unanswered
`needs_info`, per-agent wall time), SIGTERMs orphan processes, and invokes the analyzer on stall.
What does not exist is a single command that chains all of these stages into a complete, zero-touch
integration test pipeline, and a mechanism to detect the `project_complete` success signal as a
clean-exit criterion (the current watcher only handles stall paths).

## Problem

There is no single command to run a complete end-to-end integration test of jig against hn-cli.
Today, running one requires:

1. Creating a fresh project directory manually.
2. Running `jig init --auto --brief evals/projects/hn-cli/brief.md` and watching it complete.
3. Starting the orchestrator with `jig start` and monitoring until all tickets resolve (no
   automated completion signal, no timeout).
4. Manually invoking `jig eval collect <dir> --project-id hn-cli --tracer-cmd <path>` to archive
   results.

The orchestrator and post-run stages have no unattended equivalent — each requires the operator to
be present. As a result, integration tests are rarely run and require active babysitting throughout.

## Complexity drivers

- **Scale**: N/A — single project run per invocation; multiple runs are isolated by directory.
- **Concurrency**: Multiple simultaneous integration test runs are possible when each uses a
  distinct, isolated project directory. Each run must not read or write shared mutable state from
  another run.
- **Failure modes**: The highest-risk failure mode is a false positive — the command reports
  success when the application is actually broken. One known trap: the tracer exits 0 (SKIP) when
  the built artifact isn't on PATH, and `eval collect` records that as `tracer: PASS`. A stalled
  orchestrator that never completes all tickets is the second highest-risk outcome; the run must
  not be reported as successful when tickets remain open.
- **Cross-cutting policies**: N/A — no PII, auth, or secrets beyond what jig already manages.

## Constraints

- Must compose the existing `jig init --auto --brief` and `jig eval collect` commands; no
  reimplementation of init or collection logic.
- The `eval collect` invocation requires `--project-id`, `--tracer-cmd`, and a project path;
  the integration test command must supply these correctly.
- hn-cli uses no pre-committed profile; `AutoPromptHandler` accepts whatever the PM proposes, so
  `--profile` is not required per the eval runbook. If a future project fixture needs a fixed
  profile, `--profile` can be threaded through.
- Must run without operator input after the initial command.

## Requirements

- A single CLI command triggers the full pipeline: create isolated temp dir → `jig init --auto
  --brief` → `jig start` (orchestrator, unattended) → wait for completion → `jig eval collect`
  with correct `--project-id hn-cli` and `--tracer-cmd`.
- The command exits non-zero on any stage failure: init error, orchestrator error, tracer
  non-zero exit, or stall timeout expiry.
- A stall timeout must bound the run — an orchestrator that never completes all tickets must
  eventually be killed and reported as failed, not silently pass.
- Concurrent invocations must not interfere — each run operates in a fully isolated directory
  with no shared mutable state.
- The manifest is archived to `evals/runs/hn-cli/<run-id>/manifest.yaml` after each run.
- The command must not record a tracer-SKIP outcome as success.

## Non-goals

- Running integration tests against projects other than hn-cli (not feasible now; one eval project
  at a time).
- CI/CD integration or scheduling — this is a developer-invoked command.
- Interactive progress reporting beyond what the orchestrator already emits.

## Success criteria

- A developer types one command and hn-cli builds end-to-end without touching anything else.
- The command exits non-zero on failure (any stage error, tracer failure, stall timeout) and zero
  on a genuine pass (tracer PASS, all tickets resolved, no SKIP treated as pass).
- The run manifest is archived to `evals/runs/hn-cli/` after every run.

## Open questions

- [x] **Orchestrator completion criterion**: The `project_complete` JigEvent emitted by the
  orchestrator (`orchestrator.py:2369`) when all tickets reach `resolved` or `closed` state. The
  integration test runner waits for this event (via WebSocket or bus poll) before proceeding to
  `eval collect`.
- [x] **Stall timeout**: 90 minutes. Runs regularly exceed 60 minutes; 90 minutes provides a safe
  ceiling without killing legitimate long runs.

## Change log

- 2026-06-07: Initial draft (Brent Hoover)
