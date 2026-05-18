"""Collect eval metrics from a completed project's .jig/ stores."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from jig.eval.manifest import RunManifest, TracerResult


async def collect(
    project_path: Path,
    *,
    run_id: str,
    project_id: str,
    label: str | None = None,
    tracer_cmd: list[str] | None = None,
) -> RunManifest:
    """Read .jig/ stores from project_path and return a RunManifest.

    All store I/O is async; callers should run this inside asyncio.run().
    """
    from jig.analytics.store import AnalyticsStore
    from jig.store.review_comments import ReviewCommentsStore
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore
    from jig.thread import SystemEvent

    jig_dir = project_path / ".jig"

    # --- tickets ---
    ticket_store = TicketStore(jig_dir / "store" / "tickets.jsonl")
    await ticket_store.load()
    tickets = await ticket_store.list_all()
    ticket_status_counts: dict[str, int] = {}
    for t in tickets:
        key = t.status.value if hasattr(t.status, "value") else str(t.status)
        ticket_status_counts[key] = ticket_status_counts.get(key, 0) + 1

    # --- threads: SystemEvent counts ---
    thread_store = ThreadStore(jig_dir / "store" / "threads.jsonl")
    await thread_store.load()
    system_events = await thread_store.all_by_kind("system_event")
    system_event_counts: dict[str, int] = {}
    for entry in system_events:
        try:
            ev = SystemEvent.model_validate(entry.model_dump())
            key = ev.event_type
            system_event_counts[key] = system_event_counts.get(key, 0) + 1
        except Exception:
            pass

    # --- reviewer comments ---
    rc_store = ReviewCommentsStore(jig_dir / "store" / "review_comments.jsonl")
    await rc_store._collection.load()  # type: ignore[attr-defined]
    all_comment_dicts = await rc_store._collection.find()  # type: ignore[attr-defined]
    reviewer_comment_counts: dict[str, int] = {}
    for d in all_comment_dicts:
        sev = d.get("severity", "unknown")
        reviewer_comment_counts[sev] = reviewer_comment_counts.get(sev, 0) + 1

    # --- analytics: cost, duration, spawns, fix cycles ---
    analytics = AnalyticsStore(jig_dir / "store" / "analytics.jsonl")
    await analytics.load()
    completed_events = await analytics.by_kind("agent_completed")
    spawned_events = await analytics.by_kind("agent_spawned")

    total_cost = sum(
        getattr(e, "cost_estimate_usd", None) or 0.0 for e in completed_events
    )
    total_duration = sum(getattr(e, "duration_ms", 0) for e in completed_events)
    agent_spawn_count = len(spawned_events)

    # Fix cycles: spawns on tickets that already had a prior completed run.
    # Count = spawns beyond the first per ticket.
    from collections import Counter

    spawns_per_ticket: Counter[str] = Counter()
    for e in spawned_events:
        tid = getattr(e, "ticket_id", None) or getattr(e, "correlation_id", None)
        if tid:
            spawns_per_ticket[tid] += 1
    fix_cycle_count = sum(max(0, n - 1) for n in spawns_per_ticket.values())

    # --- git sha ---
    git_sha: str | None = None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            git_sha = result.stdout.strip()
    except Exception:
        pass

    # --- jig version ---
    jig_version: str | None = None
    try:
        from importlib.metadata import version

        jig_version = version("jig")
    except Exception:
        pass

    # --- tracer ---
    tracer: TracerResult | None = None
    if tracer_cmd:
        try:
            tr = subprocess.run(
                tracer_cmd,
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=120,
            )
            tracer = TracerResult(
                passed=tr.returncode == 0,
                exit_code=tr.returncode,
                stdout=tr.stdout[:4096],
                stderr=tr.stderr[:1024],
            )
        except Exception as exc:
            tracer = TracerResult(
                passed=False,
                exit_code=-1,
                stderr=str(exc),
            )

    return RunManifest(
        run_id=run_id,
        project_id=project_id,
        label=label,
        collected_at=datetime.now(timezone.utc),
        git_sha=git_sha,
        jig_version=jig_version,
        ticket_status_counts=ticket_status_counts,
        system_event_counts=system_event_counts,
        reviewer_comment_counts=reviewer_comment_counts,
        total_cost_usd=round(total_cost, 6),
        total_duration_ms=total_duration,
        agent_spawn_count=agent_spawn_count,
        fix_cycle_count=fix_cycle_count,
        tracer=tracer,
    )
