"""LLM-driven post-run analyzer.

Bundles the run's artifacts into a single ``claude-agent-sdk`` call,
parses the ``<analysis>`` markdown + ``<metrics_update>`` JSON
sections, and returns them. The wrapping :mod:`evals.watcher.analyzer`
writes ``analysis.md`` and merges the JSON update into ``metrics.json``.

Auth path: same as every other jig agent — ``CLAUDE_CODE_OAUTH_TOKEN``
via the bundled Claude Code CLI that ``claude-agent-sdk`` spawns. NO
``ANTHROPIC_API_KEY`` is involved.

Why a single ``query()`` rather than the full agent runtime: this is
a one-shot batch job over already-finalized artifacts; no MCP servers,
no ticket worktrees, no tools — just system prompt + user message.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)


@dataclass
class LLMResult:
    """What the LLM call returns to the caller."""

    analysis_md: str
    metrics_update: dict
    cost_usd: float
    tokens_in: int
    tokens_out: int
    num_turns: int


def _load_prompt() -> str:
    here = Path(__file__).resolve().parent
    return (here / "prompts" / "analyzer.md").read_text()


def _truncate(text: str, max_chars: int) -> str:
    """Cap a chunk at ``max_chars`` with a clear marker."""
    if len(text) <= max_chars:
        return text
    keep = max_chars - 80
    return text[:keep] + f"\n\n... [truncated, {len(text) - keep} more chars] ..."


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


def _summarize_tickets(rows: list[dict]) -> str:
    state: dict[str, dict] = {}
    for row in rows:
        op = row.get("_op")
        tid = row.get("_id")
        if not tid:
            continue
        if op == "insert":
            state[tid] = dict(row)
        elif op == "update":
            state.setdefault(tid, {}).update(row)
    lines = ["ticket_id,title,status,work_type,parent_id,blocked_by"]
    for tid, t in state.items():
        title = (t.get("title") or "").replace("\n", " ").replace(",", ";")
        bb = ";".join(t.get("blocked_by") or [])
        lines.append(
            f"{tid},{title[:60]},{t.get('status','')},{t.get('work_type','')},"
            f"{t.get('parent_id') or ''},{bb}"
        )
    return "\n".join(lines)


def _summarize_threads(rows: list[dict]) -> str:
    by_ticket: dict[str, list[str]] = defaultdict(list)
    for c in rows:
        if c.get("_op") != "insert":
            continue
        tid = c.get("ticket_id") or "?"
        kind = c.get("kind") or c.get("event_type") or "?"
        author = c.get("author") or ""
        ts = (c.get("created_at") or "")[11:19]
        text = (
            c.get("content")
            or c.get("text")
            or c.get("question")
            or ""
        ).strip().replace("\n", " ")
        if len(text) > 400:
            text = text[:400] + "…"
        by_ticket[tid].append(f"  [{ts}] [{kind}] {author}: {text}")

    out_chunks: list[str] = []
    for tid in sorted(by_ticket):
        out_chunks.append(f"## ticket {tid}")
        out_chunks.extend(by_ticket[tid])
        out_chunks.append("")
    return "\n".join(out_chunks)


def _summarize_analytics(rows: list[dict]) -> str:
    completions: list[str] = [
        "agent_id,status,duration_ms,tokens_in,tokens_out,cost_usd,failure"
    ]
    state_changes: list[str] = ["timestamp,ticket_id,from,to,reason"]
    for row in rows:
        if row.get("_op") != "insert":
            continue
        kind = row.get("kind")
        if kind == "agent_completed":
            completions.append(
                f"{row.get('agent_id','')},{row.get('status','')},"
                f"{row.get('duration_ms','')},{row.get('tokens_in','')},"
                f"{row.get('tokens_out','')},"
                f"{row.get('cost_estimate_usd','')},"
                f"{row.get('failure_category') or ''}"
            )
        elif kind == "ticket_state_changed":
            state_changes.append(
                f"{row.get('timestamp','')},{row.get('ticket_id','')},"
                f"{row.get('from_state','')},{row.get('to_state','')},"
                f"{row.get('reason') or ''}"
            )
    return (
        "### agent_completed\n"
        + "\n".join(completions)
        + "\n\n### ticket_state_changed\n"
        + "\n".join(state_changes)
    )


def _summarize_logs(log_files: list[Path], max_chars: int = 60_000) -> str:
    out: list[str] = []
    keep_levels = {"ERROR", "WARNING"}
    keep_substrings = (
        " text:",
        "ask_question",
        "merge failed",
        "worktree setup failed",
        "needs_info",
        "scaffold_applied",
        "phase ",
    )
    total = 0
    for path in sorted(log_files):
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            level = row.get("level", "")
            msg = row.get("msg", "")
            if level in keep_levels or any(s in msg for s in keep_substrings):
                ts = (row.get("ts") or "")[11:23]
                role = row.get("role") or ""
                ln = f"[{ts}] {level:7} {role:18} {msg[:300]}"
                out.append(ln)
                total += len(ln)
                if total > max_chars:
                    out.append(f"... [log truncated at {max_chars} chars] ...")
                    return "\n".join(out)
    return "\n".join(out)


def _git_log(project_path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--oneline", "--decorate", "-50"],
            cwd=str(project_path),
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"(git log unavailable: {exc})"


def _build_user_prompt(
    *,
    metrics_json: dict,
    brief_text: str,
    tickets: str,
    threads: str,
    analytics: str,
    logs: str,
    git_log: str,
) -> str:
    return f"""Analyze the following eval run artifacts and produce
the analysis report and metrics update per your system prompt.

## metrics.json (programmatic, already computed)
```json
{json.dumps(metrics_json, indent=2)}
```

## brief.md (source spec)
{_truncate(brief_text, 12_000)}

## Ticket store summary (CSV)
{_truncate(tickets, 8_000)}

## Thread summary (per-ticket)
{_truncate(threads, 60_000)}

## Analytics summary (CSV)
{_truncate(analytics, 12_000)}

## Daemon log highlights
{_truncate(logs, 60_000)}

## Git log (last 50 across all branches)
{_truncate(git_log, 6_000)}
"""


def _parse_response(text: str) -> tuple[str, dict]:
    """Extract <analysis>...</analysis> + <metrics_update>...</metrics_update>."""
    analysis_match = re.search(
        r"<analysis>(.*?)</analysis>", text, re.DOTALL | re.IGNORECASE
    )
    update_match = re.search(
        r"<metrics_update>(.*?)</metrics_update>", text, re.DOTALL | re.IGNORECASE
    )
    analysis = analysis_match.group(1).strip() if analysis_match else text.strip()

    update: dict = {}
    if update_match:
        block = update_match.group(1).strip()
        block = re.sub(r"^```(?:json)?\s*", "", block)
        block = re.sub(r"\s*```$", "", block)
        try:
            update = json.loads(block)
        except json.JSONDecodeError:
            update = {}
    return analysis, update


async def _run_query(
    *, system_prompt: str, user_prompt: str, cwd: Path
) -> tuple[str, ResultMessage | None]:
    """Drive a one-shot ``claude-agent-sdk`` query and accumulate output."""
    options = ClaudeAgentOptions(
        cwd=str(cwd),
        system_prompt=system_prompt,
        allowed_tools=[],
        permission_mode="bypassPermissions",
    )
    chunks: list[str] = []
    result: ResultMessage | None = None
    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content or []:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
        elif isinstance(message, ResultMessage):
            result = message
    return "".join(chunks), result


def run_llm_analysis(
    *,
    project_path: Path,
    metrics: dict,
    brief_path: Path | None,
) -> LLMResult:
    """Make the SDK call and parse the response."""
    store = project_path / ".jig" / "store"
    tickets_rows = _read_jsonl(store / "tickets.jsonl")
    comments_rows = _read_jsonl(store / "comments.jsonl")
    analytics_rows = _read_jsonl(store / "analytics.jsonl")
    log_files = sorted((project_path / ".jig" / "logs").glob("*.jsonl"))

    brief_text = ""
    if brief_path and brief_path.is_file():
        brief_text = brief_path.read_text()

    user_prompt = _build_user_prompt(
        metrics_json=metrics,
        brief_text=brief_text,
        tickets=_summarize_tickets(tickets_rows),
        threads=_summarize_threads(comments_rows),
        analytics=_summarize_analytics(analytics_rows),
        logs=_summarize_logs(log_files),
        git_log=_git_log(project_path),
    )
    system_prompt = _load_prompt()

    text, result = asyncio.run(
        _run_query(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            cwd=project_path,
        )
    )
    analysis, update = _parse_response(text)

    cost = (result.total_cost_usd or 0.0) if result else 0.0
    usage = (result.usage or {}) if result else {}
    tokens_in = int(
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )
    tokens_out = int(usage.get("output_tokens") or 0)
    num_turns = 1  # one-shot — ResultMessage doesn't carry num_turns directly

    return LLMResult(
        analysis_md=analysis,
        metrics_update=update,
        cost_usd=round(cost, 4),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        num_turns=num_turns,
    )
