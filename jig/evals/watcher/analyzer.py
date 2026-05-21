"""Programmatic post-run analyzer (v1 minimal).

Reads a finished eval project's ``.jig/store/*.jsonl`` + ``.jig/logs/*``
+ git history and emits:

  - ``evals/runs/<run-id>/metrics.json`` — machine-readable summary
    matching ``RunMetrics`` (see ``metrics.py``).
  - ``evals/runs/<run-id>/analysis.md`` — human-readable summary
    (currently a programmatic template; LLM-driven narrative is a
    follow-up slice).

Usage:

    python -m jig.evals.watcher.analyzer /abs/path/to/jig_evals/hn-cli
    python -m jig.evals.watcher.analyzer --project hn-cli

    # Custom run_id (default: <project>-<UTC ISO compact>):
    python -m jig.evals.watcher.analyzer --project hn-cli --run-id manual-1

    # Where to write outputs (default: <jig_repo>/evals/runs/<run-id>):
    python -m jig.evals.watcher.analyzer --project hn-cli --out-dir /tmp/runs/x
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from jig.evals.watcher.metrics import (
    AgentMetrics,
    OperatorQuestionMetrics,
    Outcome,
    PhaseMetric,
    RunMetrics,
    StallMetrics,
    TicketMetrics,
)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _resolve_eval_project(name: str) -> Path:
    here = (
        Path(__file__).resolve().parents[3]
    )  # jig/evals/watcher/../../.. -> repo root
    workspace = here.parent / "jig_evals" / name
    return workspace


def _project_jig_commit(jig_repo: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(jig_repo),
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip()[:12]
    except Exception:  # noqa: BLE001
        return ""


def _ticket_state_history(rows: list[dict]) -> dict[str, dict]:
    """Reduce JSONL inserts/updates to the latest state per ticket id."""
    state: dict[str, dict] = {}
    for row in rows:
        op = row.get("_op")
        tid = row.get("_id")
        if not tid:
            continue
        if op == "insert":
            state[tid] = {**row}
        elif op == "update":
            state.setdefault(tid, {}).update(row)
    return state


def _classify_tickets(state: dict[str, dict]) -> TicketMetrics:
    total = len(state)
    resolved = sum(1 for s in state.values() if s.get("status") == "resolved")
    failed = sum(1 for s in state.values() if s.get("status") == "failed")
    return TicketMetrics(total=total, resolved=resolved, failed=failed)


_PHASE_NAME_RE = re.compile(r"^phase (?P<phase>[a-z_]+):\s*(?P<status>\w+)")


def _classify_phases(comments: list[dict]) -> tuple[dict[str, PhaseMetric], int]:
    """Walk system_event comments to count per-phase runs / retries.

    A "retry" is counted as any phase_run on a ticket where that phase
    has already run at least once.
    Returns (phases dict, review_blocks count).
    """
    runs: dict[str, list[str]] = defaultdict(list)  # phase -> [ticket_id]
    review_blocks = 0
    for c in comments:
        if c.get("_op") != "insert":
            continue
        if c.get("event_type") != "agent_run":
            continue
        payload = c.get("payload") or {}
        phase = payload.get("phase") or payload.get("role") or "unknown"
        tid = c.get("ticket_id") or "?"
        runs[phase].append(tid)
    # Count avg_turns from agent_run num_turns
    turns_by_phase: dict[str, list[int]] = defaultdict(list)
    for c in comments:
        if c.get("_op") != "insert":
            continue
        if c.get("event_type") != "agent_run":
            continue
        payload = c.get("payload") or {}
        phase = payload.get("phase") or payload.get("role") or "unknown"
        n = payload.get("num_turns")
        if isinstance(n, (int, float)) and n > 0:
            turns_by_phase[phase].append(int(n))

    phases: dict[str, PhaseMetric] = {}
    for phase, ticket_ids in runs.items():
        run_count = len(ticket_ids)
        # Retries: any ticket with > 1 run for this phase contributes
        # (count - 1) retries.
        per_ticket: dict[str, int] = defaultdict(int)
        for tid in ticket_ids:
            per_ticket[tid] += 1
        retries = sum(max(0, n - 1) for n in per_ticket.values())
        avg_turns = sum(turns_by_phase.get(phase, [])) / max(
            1, len(turns_by_phase.get(phase, []))
        )
        phases[phase] = PhaseMetric(
            runs=run_count, retries=retries, avg_turns=round(avg_turns, 2)
        )
        if phase == "review":
            review_blocks += retries
    return phases, review_blocks


def _aggregate_agents(comments: list[dict], analytics: list[dict]) -> AgentMetrics:
    """Aggregate agent activity from two sources:

    - ``comments.jsonl`` ``agent_run`` SystemEvents → turn counts.
    - ``analytics.jsonl`` ``agent_completed`` events → cost + tokens
      (the SDK numbers wired into AgentCompleted analytics).
    """
    total_turns = 0
    for c in comments:
        if c.get("_op") != "insert":
            continue
        if c.get("event_type") != "agent_run":
            continue
        n = (c.get("payload") or {}).get("num_turns")
        if isinstance(n, (int, float)):
            total_turns += int(n)

    total_cost = 0.0
    total_in = 0
    total_out = 0
    for a in analytics:
        if a.get("_op") != "insert":
            continue
        if a.get("kind") != "agent_completed":
            continue
        cost = a.get("cost_estimate_usd")
        if isinstance(cost, (int, float)):
            total_cost += float(cost)
        ti = a.get("tokens_in")
        if isinstance(ti, (int, float)):
            total_in += int(ti)
        to = a.get("tokens_out")
        if isinstance(to, (int, float)):
            total_out += int(to)

    return AgentMetrics(
        total_turns=total_turns,
        total_cost_usd=round(total_cost, 4),
        total_tokens_in=total_in,
        total_tokens_out=total_out,
    )


def _operator_questions(comments: list[dict]) -> OperatorQuestionMetrics:
    """Count agent → operator questions (Question entries with author=agent
    and assignee=user, or kind=question_to_operator).

    The "verifiable_in_hindsight" / "product_scope" split needs LLM
    judgment — leave at 0 for v1.
    """
    asked = 0
    for c in comments:
        if c.get("_op") != "insert":
            continue
        kind = c.get("kind") or c.get("event_type") or ""
        if kind in ("question", "question_to_operator", "Question"):
            asked += 1
    return OperatorQuestionMetrics(
        asked=asked, verifiable_in_hindsight=0, product_scope=asked
    )


def _count_merge_failures(comments: list[dict]) -> int:
    n = 0
    for c in comments:
        if c.get("_op") != "insert":
            continue
        if c.get("event_type") in (
            "provisioning_failed",
            "auto_commit_failed",
            "dep_merge_failed",
        ):
            n += 1
        # Also catch the merge-time error logged as a SystemEvent on the
        # parent ticket.
        content = (c.get("content") or "").lower()
        if "merge failed" in content or "refusing to merge" in content:
            n += 1
    return n


def _outcome_from_state(
    state: dict[str, dict], tickets: TicketMetrics
) -> tuple[Outcome, str]:
    if tickets.failed > 0:
        return "failed", f"{tickets.failed} ticket(s) ended in failed state"
    if tickets.total == 0:
        return "failed", "no tickets created"
    if all(s.get("status") in ("resolved", "closed") for s in state.values()):
        return "succeeded", "all tickets resolved"
    in_flight = [
        s
        for s in state.values()
        if s.get("status") in ("open", "in_progress", "needs_info", "blocked")
    ]
    if in_flight:
        return "stalled", f"{len(in_flight)} ticket(s) still open at analyze time"
    return "succeeded", "no failed tickets"


def _run_window(rows: list[dict]) -> tuple[str, str, float]:
    """Earliest created_at / latest updated_at across a JSONL row set."""
    starts: list[str] = []
    ends: list[str] = []
    for row in rows:
        ts = row.get("created_at") or row.get("updated_at") or row.get("ts")
        if isinstance(ts, str):
            (starts if row.get("_op") == "insert" else ends).append(ts)
            ends.append(ts)
    if not starts:
        return "", "", 0.0
    started = min(starts)
    ended = max(ends) if ends else started

    def _parse(s: str) -> datetime | None:
        try:
            # Strip trailing 'Z' if present.
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None

    s_dt = _parse(started)
    e_dt = _parse(ended)
    duration = (e_dt - s_dt).total_seconds() if s_dt and e_dt else 0.0
    return started, ended, round(duration, 1)


def analyze(
    *,
    project_path: Path,
    run_id: str,
    out_dir: Path,
    jig_repo: Path,
    project_name: str,
    tags: list[str] | None = None,
    use_llm: bool = True,
    brief_path: Path | None = None,
) -> RunMetrics:
    store = project_path / ".jig" / "store"
    tickets_rows = _read_jsonl(store / "tickets.jsonl")
    comments_rows = _read_jsonl(store / "comments.jsonl")
    analytics_rows = _read_jsonl(store / "analytics.jsonl")

    state = _ticket_state_history(tickets_rows)
    tickets = _classify_tickets(state)
    phases, review_blocks = _classify_phases(comments_rows)
    agents = _aggregate_agents(comments_rows, analytics_rows)
    operator_q = _operator_questions(comments_rows)
    merge_failures = _count_merge_failures(comments_rows)

    started_at, ended_at, duration_s = _run_window(tickets_rows + comments_rows)
    outcome, reason = _outcome_from_state(state, tickets)

    metrics = RunMetrics(
        run_id=run_id,
        project=project_name,
        outcome=outcome,
        outcome_reason=reason,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=duration_s,
        tickets=tickets,
        phases=phases,
        agents=agents,
        operator_questions=operator_q,
        stalls=StallMetrics(),  # live watcher fills this; analyzer leaves it empty
        merge_failures=merge_failures,
        review_blocks=review_blocks,
        jig_commit=_project_jig_commit(jig_repo),
        tags=list(tags or []),
    )

    out_dir.mkdir(parents=True, exist_ok=True)

    # Optionally run the LLM-driven narrative pass. Lazily imported so
    # the programmatic mode doesn't pull claude-agent-sdk on every run.
    llm_summary_extras: list[str] = []
    if use_llm:
        try:
            from jig.evals.watcher.llm import run_llm_analysis

            llm = run_llm_analysis(
                project_path=project_path,
                metrics=metrics.model_dump(),
                brief_path=brief_path,
            )
            # Merge structured update into metrics (additive only).
            update = llm.metrics_update or {}
            if "operator_questions" in update and isinstance(
                update["operator_questions"], dict
            ):
                for k, v in update["operator_questions"].items():
                    if hasattr(metrics.operator_questions, k):
                        setattr(metrics.operator_questions, k, v)
            if "tags" in update and isinstance(update["tags"], list):
                for t in update["tags"]:
                    if isinstance(t, str) and t not in metrics.tags:
                        metrics.tags.append(t)
            (out_dir / "analysis.md").write_text(llm.analysis_md + "\n")
            llm_summary_extras.append(
                f"[analyzer] LLM cost=${llm.cost_usd:.4f} "
                f"tokens_in={llm.tokens_in:,} tokens_out={llm.tokens_out:,}"
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[analyzer] LLM pass failed: {exc!r}; falling back to "
                "programmatic narrative",
                file=sys.stderr,
            )
            (out_dir / "analysis.md").write_text(_render_summary_md(metrics))
    else:
        (out_dir / "analysis.md").write_text(_render_summary_md(metrics))

    # Write metrics.json AFTER the LLM merge so verifiable_in_hindsight
    # / extra tags appear in the persisted file.
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics.model_dump(), indent=2, sort_keys=False) + "\n"
    )

    for line in llm_summary_extras:
        print(line)

    return metrics


def _render_summary_md(m: RunMetrics) -> str:
    lines: list[str] = []
    lines.append(f"# Eval analysis — {m.project} ({m.run_id})\n")
    lines.append(f"**Outcome:** `{m.outcome}` — {m.outcome_reason}\n")
    lines.append(
        f"**Duration:** {m.duration_s:.0f}s "
        f"(start `{m.started_at}` → end `{m.ended_at}`)\n"
    )
    lines.append(f"**Jig commit:** `{m.jig_commit}`\n")
    lines.append("")

    lines.append("## Tickets")
    lines.append(
        f"- total: {m.tickets.total} · resolved: {m.tickets.resolved} · "
        f"failed: {m.tickets.failed}"
    )
    lines.append("")

    if m.phases:
        lines.append("## Phases")
        lines.append("| phase | runs | retries | avg turns |")
        lines.append("|---|---|---|---|")
        for name, p in sorted(m.phases.items()):
            lines.append(f"| {name} | {p.runs} | {p.retries} | {p.avg_turns:.1f} |")
        lines.append("")

    lines.append("## Agents")
    lines.append(
        f"- total turns: {m.agents.total_turns} · cost: "
        f"${m.agents.total_cost_usd:.4f} · "
        f"tokens in/out: {m.agents.total_tokens_in:,}/"
        f"{m.agents.total_tokens_out:,}"
    )
    lines.append("")

    lines.append("## Operator questions")
    lines.append(
        f"- asked: {m.operator_questions.asked} · "
        f"product/scope: {m.operator_questions.product_scope} · "
        f"verifiable in hindsight: "
        f"{m.operator_questions.verifiable_in_hindsight} "
        f"_(LLM-judged; 0 in v1)_"
    )
    lines.append("")

    if m.merge_failures or m.review_blocks:
        lines.append("## Friction")
        if m.merge_failures:
            lines.append(f"- merge failures: {m.merge_failures}")
        if m.review_blocks:
            lines.append(f"- review blocks: {m.review_blocks}")
        lines.append("")

    if m.stalls.detected:
        lines.append(
            f"## Stalls\n- signal: `{m.stalls.signal}` (count: {m.stalls.detected})\n"
        )

    if m.tags:
        lines.append(f"_tags: {', '.join(m.tags)}_\n")

    lines.append(
        "> _Generated by `evals/watcher/analyzer.py` — programmatic "
        "metrics only. LLM-driven narrative (trajectory, friction, "
        "recommendations) is a follow-up slice._"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "project_path",
        nargs="?",
        help="absolute path to the jig project workspace",
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        help="resolve project_path from <jig_repo>/../jig_evals/<NAME>/",
    )
    parser.add_argument(
        "--run-id",
        help="run identifier (default: <project>-<UTC ISO compact>)",
    )
    parser.add_argument(
        "--out-dir",
        help="where to write metrics.json + analysis.md "
        "(default: <jig_repo>/evals/runs/<run-id>)",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="free-form label (repeatable) — written to metrics.tags",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help=(
            "skip the LLM narrative pass and emit only programmatic "
            "metrics + a template analysis.md. Default: run the LLM."
        ),
    )
    parser.add_argument(
        "--brief",
        metavar="PATH",
        help=(
            "path to the source brief.md used by the LLM for fidelity "
            "analysis. Defaults to <jig_repo>/evals/projects/<NAME>/brief.md "
            "when --project is given."
        ),
    )
    args = parser.parse_args(argv)

    if args.project:
        if args.project_path:
            print(
                "error: --project is mutually exclusive with project_path",
                file=sys.stderr,
            )
            return 2
        proj = _resolve_eval_project(args.project)
        project_name = args.project
    else:
        if not args.project_path:
            print(
                "error: project_path is required (or use --project NAME)",
                file=sys.stderr,
            )
            return 2
        proj = Path(args.project_path)
        project_name = proj.name

    if not proj.is_absolute():
        print(f"error: project_path must be absolute: {proj}", file=sys.stderr)
        return 2
    if not proj.is_dir():
        print(f"error: not a directory: {proj}", file=sys.stderr)
        return 2

    jig_repo = Path(__file__).resolve().parents[3]
    run_id = (
        args.run_id
        or f"{project_name}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    out_dir = (
        Path(args.out_dir) if args.out_dir else jig_repo / "evals" / "runs" / run_id
    )

    brief_path: Path | None = None
    if args.brief:
        brief_path = Path(args.brief)
    elif args.project:
        candidate = jig_repo / "evals" / "projects" / args.project / "brief.md"
        if candidate.is_file():
            brief_path = candidate

    metrics = analyze(
        project_path=proj,
        run_id=run_id,
        out_dir=out_dir,
        jig_repo=jig_repo,
        project_name=project_name,
        tags=args.tag,
        use_llm=not args.no_llm,
        brief_path=brief_path,
    )

    print(f"[analyzer] outcome={metrics.outcome} duration={metrics.duration_s:.0f}s")
    print(f"[analyzer] wrote {out_dir / 'metrics.json'}")
    print(f"[analyzer] wrote {out_dir / 'analysis.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
