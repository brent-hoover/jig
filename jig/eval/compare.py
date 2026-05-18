"""Compare two RunManifests and emit a markdown table."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from jig.eval.manifest import RunManifest


def _load_manifest(path: Path) -> RunManifest:
    data = yaml.safe_load(path.read_text()) or {}
    return RunManifest.model_validate(data)


def _find_manifest(runs_root: Path, project_id: str, label: str) -> Path:
    """Resolve a label or run-id to a manifest path."""
    base = runs_root / project_id
    if not base.exists():
        raise FileNotFoundError(f"No runs found for project '{project_id}'")
    # Try exact run-id directory
    exact = base / label / "manifest.yaml"
    if exact.exists():
        return exact
    # Try matching label field across all runs
    for run_dir in sorted(base.iterdir()):
        m = run_dir / "manifest.yaml"
        if m.exists():
            data = yaml.safe_load(m.read_text()) or {}
            if data.get("label") == label or data.get("run_id") == label:
                return m
    raise FileNotFoundError(f"No manifest with label or run_id '{label}' under {base}")


def _delta(before: Any, after: Any) -> str:
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        diff = after - before
        sign = "+" if diff >= 0 else ""
        if isinstance(before, float) or isinstance(after, float):
            return f"{sign}{diff:.4f}"
        return f"{sign}{diff}"
    return "" if before == after else f"{before} → {after}"


def compare_manifests(before: RunManifest, after: RunManifest) -> str:
    """Return a markdown table comparing two manifests."""
    lines: list[str] = [
        f"## Eval comparison: {before.project_id}",
        "",
        f"Before: `{before.label or before.run_id}` ({before.collected_at.date()})",
        f"After:  `{after.label or after.run_id}` ({after.collected_at.date()})",
        "",
        "| Metric | Before | After | Delta |",
        "| --- | --- | --- | --- |",
    ]

    def row(name: str, b: Any, a: Any) -> str:
        return f"| {name} | {b} | {a} | {_delta(b, a)} |"

    # Ticket outcomes
    all_statuses = sorted(
        set(before.ticket_status_counts) | set(after.ticket_status_counts)
    )
    for status in all_statuses:
        b = before.ticket_status_counts.get(status, 0)
        a = after.ticket_status_counts.get(status, 0)
        lines.append(row(f"tickets/{status}", b, a))

    # Error events (only show non-zero in either)
    error_types = sorted(
        set(before.system_event_counts) | set(after.system_event_counts)
    )
    for etype in error_types:
        b = before.system_event_counts.get(etype, 0)
        a = after.system_event_counts.get(etype, 0)
        if b or a:
            lines.append(row(f"system_event/{etype}", b, a))

    # Reviewer comments
    severities = sorted(
        set(before.reviewer_comment_counts) | set(after.reviewer_comment_counts)
    )
    for sev in severities:
        b = before.reviewer_comment_counts.get(sev, 0)
        a = after.reviewer_comment_counts.get(sev, 0)
        lines.append(row(f"reviewer/{sev}", b, a))

    # Cost + throughput
    lines.append(row("cost_usd", before.total_cost_usd, after.total_cost_usd))
    lines.append(row("duration_ms", before.total_duration_ms, after.total_duration_ms))
    lines.append(row("agent_spawns", before.agent_spawn_count, after.agent_spawn_count))
    lines.append(row("fix_cycles", before.fix_cycle_count, after.fix_cycle_count))

    # Tracer
    b_tracer = (
        "pass"
        if before.tracer and before.tracer.passed
        else ("fail" if before.tracer else "n/a")
    )
    a_tracer = (
        "pass"
        if after.tracer and after.tracer.passed
        else ("fail" if after.tracer else "n/a")
    )
    lines.append(row("tracer", b_tracer, a_tracer))

    return "\n".join(lines)
