"""Per-commit reviewer runner -- invoked by the post-commit hook.

This is the script the per-commit ``post-commit`` hook calls (via
``jig per-commit-review --ticket <id>``). It loads the project's
ticket store, dispatches the mechanical reviewer subset against the
ticket's worktree, persists each comment to the ReviewCommentsStore,
emits a ``PerCommitCheckFailed`` analytics event for each critical
finding, and prints comments to stderr so the dev agent's terminal
shows them inline with the commit output.

Exit code is **always 0** per design -- the per-commit cadence is
informational, not blocking. The intent is "catch the mistake before
the dev compounds it" via fast feedback, not "freeze the worktree
on disagreement."
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click

_logger = logging.getLogger(__name__)


def _find_project_root(worktree_path: Path) -> Path | None:
    """Walk up from a ticket worktree to find the project root.

    Convention: ``<project>/.jig/worktrees/<ticket_id>/``. The project
    root is two parents up from the worktree dir.
    """
    parent = worktree_path.parent
    grandparent = parent.parent
    project_root = grandparent.parent
    if parent.name == "worktrees" and grandparent.name == ".jig":
        return project_root
    return None


async def _run_per_commit_review(ticket_id: str, *, cwd: Path) -> int:
    """Core async entry point. Returns the count of critical comments."""
    from jig.analytics.emitter import EventEmitter
    from jig.analytics.events import PerCommitCheckFailed
    from jig.analytics.store import AnalyticsStore
    from jig.reviewers.comment import Severity
    from jig.reviewers.dispatch import dispatch_for_cadence
    from jig.store.review_comments import ReviewCommentsStore
    from jig.store.tickets import TicketStore

    project_root = _find_project_root(cwd)
    if project_root is None:
        candidate = cwd
        while candidate != candidate.parent:
            if (candidate / ".jig").is_dir():
                project_root = candidate
                break
            candidate = candidate.parent
        if project_root is None:
            print(
                f"per-commit-review: cannot find project root from {cwd}",
                file=sys.stderr,
            )
            return 0

    store_dir = project_root / ".jig" / "store"
    if not store_dir.is_dir():
        print(
            f"per-commit-review: no store dir at {store_dir}; skipping",
            file=sys.stderr,
        )
        return 0

    tickets = TicketStore(store_dir / "tickets.jsonl")
    await tickets.load()
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        print(
            f"per-commit-review: ticket {ticket_id!r} not found; skipping",
            file=sys.stderr,
        )
        return 0

    review_store = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    await review_store.load()

    analytics = AnalyticsStore(store_dir / "analytics.jsonl")
    await analytics.load()
    emitter = EventEmitter(analytics)

    by_reviewer = await dispatch_for_cadence(
        ticket,
        project_root,
        "per_commit",
        worktree_path=cwd,
    )

    commit_sha = await _read_head_sha(cwd)

    critical_count = 0
    for reviewer_id, comments in by_reviewer.items():
        # Block 3 design contract: per-commit cadence runs
        # mechanical-only (single-digit-second budget). LLM-spawn
        # pendings only ever appear at end_of_ticket cadence; if one
        # somehow shows up here it's a routing bug and we skip it
        # rather than crash the post-commit hook.
        if not isinstance(comments, list):
            continue
        for comment in comments:
            stamped = comment.model_copy(update={"ticket_id": ticket_id})
            await review_store.append(stamped)
            print(
                f"[per-commit:{reviewer_id}] {stamped.severity} {stamped.type}: "
                f"{stamped.prose}",
                file=sys.stderr,
            )
            if stamped.severity in (Severity.CRITICAL, Severity.CRITICAL.value):
                critical_count += 1
                role = _reviewer_role_for_event(reviewer_id)
                if role is None:
                    continue
                await emitter.emit(
                    PerCommitCheckFailed(
                        ticket_id=ticket_id,
                        agent_id=f"per-commit-runner:{ticket_id[:8]}",
                        commit_sha=commit_sha,
                        reviewer_role=role,  # type: ignore[arg-type]
                        violation_category=str(stamped.type),
                        contract_uri=stamped.contract_uri,
                        severity=stamped.severity,  # type: ignore[arg-type]
                        auto_applied=False,
                    )
                )

    return critical_count


def _reviewer_role_for_event(reviewer_id: str) -> str | None:
    return {
        "contract-compliance": "contract_compliance",
        "cross-cutting-policy": "cross_cutting_policy",
        "spec-compliance": "spec_compliance",
    }.get(reviewer_id)


async def _read_head_sha(worktree_path: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "rev-parse",
        "HEAD",
        cwd=worktree_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return "unknown"
    return stdout.decode("utf-8", errors="replace").strip() or "unknown"


@click.command("per-commit-review")
@click.option(
    "--ticket",
    "ticket_id",
    required=True,
    help="Ticket id whose worktree just received a commit.",
)
@click.option(
    "--cwd",
    "cwd",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Worktree path (defaults to the current directory).",
)
def main(ticket_id: str, cwd: Path | None) -> None:
    """CLI entry point. Always exits 0 per design."""
    target = cwd if cwd is not None else Path.cwd()
    try:
        asyncio.run(_run_per_commit_review(ticket_id, cwd=target))
    except Exception as exc:  # noqa: BLE001
        print(
            f"per-commit-review: unexpected error {exc!r}; continuing",
            file=sys.stderr,
        )
        # SF-I4: emit a durable thread + analytics event so a runner
        # crash isn't indistinguishable from "review passed". Exit
        # stays 0 so the post-commit hook stays non-blocking.
        try:
            asyncio.run(_record_runner_crash(ticket_id, cwd=target, error=str(exc)))
        except Exception:  # noqa: BLE001
            print(
                "per-commit-review: also failed to record crash event; "
                "see ticket thread for partial state",
                file=sys.stderr,
            )
    sys.exit(0)


async def _record_runner_crash(ticket_id: str, *, cwd: Path, error: str) -> None:
    """Record a ``per_commit_runner_crashed`` SystemEvent on the
    ticket thread. Best-effort: if the project store can't be reached
    (e.g. corrupted directory), the caller's outer except prints a
    fallback message and the runner still exits 0."""
    from jig.store.threads import ThreadStore
    from jig.thread import SystemEvent

    project_root = _find_project_root(cwd)
    if project_root is None:
        candidate = cwd
        while candidate != candidate.parent:
            if (candidate / ".jig").is_dir():
                project_root = candidate
                break
            candidate = candidate.parent
        if project_root is None:
            return

    store_dir = project_root / ".jig" / "store"
    if not store_dir.is_dir():
        return

    threads = ThreadStore(store_dir / "threads.jsonl")
    await threads.load()
    await threads.post(
        SystemEvent(
            ticket_id=ticket_id,
            author="per-commit-runner",
            event_type="per_commit_runner_crashed",
            content=(
                f"per-commit reviewer runner crashed (non-blocking): "
                f"{error}. Mechanical reviews for this commit did not run."
            ),
        )
    )


__all__ = ["main"]
