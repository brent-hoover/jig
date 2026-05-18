"""Cascade audit-trail viewer (Track C Final, Deliverable 2).

Operator-facing helpers powering the ``jig sa cascade list/show/audit``
CLI commands. Reads + renders cascade proposals + their JSONL audit log
without coupling the CLI to YAML/JSONL parsing.

Three views per the task spec:

- ``list_cascades`` — every cascade on disk (resolved + pending +
  rejected + holding + staged), one row per cascade with state + age.
- ``show_cascade`` — full proposal + per-cascade audit slice + current
  state for one cascade.
- ``filter_audit`` — flat audit log filtered by date / actor for the
  cross-project rejection-pattern analytics use case.

The CLI lives in ``jig.cli`` (``sa cascade`` subgroup); the formatting
helpers here are pure (no I/O beyond the initial reads) so tests can
introspect the dataclasses directly without scraping stdout.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jig.schemas.arch import (
    CascadeAuditEntry,
    CascadeProposal,
)
from jig.spec_loader import (
    cascade_audit_path,
    cascades_dir,
)

__all__ = [
    "CascadeListRow",
    "CascadeShowResult",
    "filter_audit",
    "format_audit_markdown",
    "format_list",
    "format_show",
    "list_cascades",
    "load_audit",
    "show_cascade",
]


class CascadeListRow(BaseModel):
    """One row in ``jig sa cascade list`` output.

    Slim projection of CascadeProposal — just enough for an at-a-glance
    operator scan; the full proposal is one ``cascade show`` away.
    """

    model_config = ConfigDict(extra="forbid")

    cascade_id: str
    risk_id: str
    state: str
    contract_count: int
    holding_for: str | None = None
    generated_at: datetime
    rejected_reason: str | None = None


class CascadeShowResult(BaseModel):
    """Full detail row for ``jig sa cascade show``.

    Combines the on-disk proposal with the audit-log slice for this
    cascade so the renderer doesn't have to re-load both.
    """

    model_config = ConfigDict(extra="forbid")

    proposal: CascadeProposal
    audit_entries: list[CascadeAuditEntry] = Field(default_factory=list)


def _load_proposal(path: Path) -> CascadeProposal | None:
    """Load one cascade proposal; return None on parse error.

    Malformed YAML in the cascades dir shouldn't crash the viewer —
    operators investigating "what's in flight" need the rest of the
    cascades visible. The CLI surfaces parse failures separately.
    """
    try:
        return CascadeProposal.model_validate(yaml.safe_load(path.read_text()))
    except Exception:
        return None


def _all_proposal_paths(project_path: Path) -> list[Path]:
    """Every cascade proposal YAML under ``.jig/arch/cascades/``.

    Excludes the audit log (audit.jsonl). Sorted by filename so ts-
    suffix preserves chronological order in operator output.
    """
    target_dir = cascades_dir(project_path)
    if not target_dir.is_dir():
        return []
    return sorted(p for p in target_dir.glob("*.yaml") if p.is_file())


def list_cascades(project_path: Path) -> list[CascadeListRow]:
    """Return one ``CascadeListRow`` per cascade on disk.

    Sorted by ``generated_at`` ascending so the operator reads the
    audit trail in chronological order. Empty list when the cascades
    dir doesn't exist (no spike has fired confirmed_impossible yet).
    """
    rows: list[CascadeListRow] = []
    for path in _all_proposal_paths(project_path):
        prop = _load_proposal(path)
        if prop is None:
            continue
        rows.append(
            CascadeListRow(
                cascade_id=prop.cascade_id,
                risk_id=prop.risk_id,
                state=prop.state.value,
                contract_count=len(prop.contracts),
                holding_for=prop.holding_for,
                generated_at=prop.generated_at,
                rejected_reason=prop.rejected_reason,
            )
        )
    return sorted(rows, key=lambda r: r.generated_at)


def load_audit(project_path: Path) -> list[CascadeAuditEntry]:
    """Read the full cascade audit log; empty list when absent."""
    path = cascade_audit_path(project_path)
    if not path.is_file():
        return []
    out: list[CascadeAuditEntry] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(CascadeAuditEntry.model_validate(json.loads(line)))
        except Exception:
            # Skip corrupt rows rather than block the viewer; the
            # writer is the only path that creates them so a corrupt
            # row signals an editor accident the operator can fix.
            continue
    return out


def show_cascade(project_path: Path, cascade_id: str) -> CascadeShowResult:
    """Build a full-detail show payload for one cascade.

    Raises ``KeyError`` when no proposal artifact matches the id —
    surfacing typos as a structured error rather than printing
    "(empty)" on a silently-absent target.
    """
    target = cascades_dir(project_path) / f"{cascade_id}.yaml"
    if not target.is_file():
        raise KeyError(
            f"no cascade proposal at {target} — "
            "use ``jig sa cascade list`` to see the available ids"
        )
    proposal = _load_proposal(target)
    if proposal is None:
        raise ValueError(
            f"cascade proposal at {target} failed to parse — "
            "fix the YAML or remove the file"
        )
    entries = [e for e in load_audit(project_path) if e.cascade_id == cascade_id]
    return CascadeShowResult(
        proposal=proposal,
        audit_entries=sorted(entries, key=lambda e: e.timestamp),
    )


def filter_audit(
    project_path: Path,
    *,
    since: datetime | None = None,
    actor: str | None = None,
) -> list[CascadeAuditEntry]:
    """Return audit entries filtered by ``since`` (inclusive) and/or ``actor``.

    Both filters compose (AND-semantics). ``since`` interpreted as
    UTC-aware (callers pass parsed dates from the CLI's ``--since``);
    ``actor`` is exact-match on the ``actor`` field. Empty filters →
    every entry, sorted by timestamp ascending.
    """
    entries: Iterable[CascadeAuditEntry] = load_audit(project_path)
    if since is not None:
        entries = (e for e in entries if e.timestamp >= since)
    if actor is not None:
        entries = (e for e in entries if e.actor == actor)
    return sorted(entries, key=lambda e: e.timestamp)


# ---- formatters ---------------------------------------------------------


def _ts(dt: datetime) -> str:
    """Compact ISO-second precision; UTC implied."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_list(rows: list[CascadeListRow]) -> str:
    """Render ``list_cascades`` rows as a fixed-column ASCII table.

    Plain stdout — no colors, no rich; the operator pipes this into
    grep/awk and we don't fight that. Empty input returns a single-line
    "(no cascades)" so the absence-of-cascades signal is unambiguous.
    """
    if not rows:
        return "(no cascades)"
    headers = ("CASCADE_ID", "RISK", "STATE", "CONTRACTS", "HOLDING_FOR", "GENERATED")
    table_rows = [
        (
            r.cascade_id,
            r.risk_id,
            r.state,
            str(r.contract_count),
            r.holding_for or "-",
            _ts(r.generated_at),
        )
        for r in rows
    ]
    widths = [
        max(len(h), max((len(row[i]) for row in table_rows), default=0))
        for i, h in enumerate(headers)
    ]
    lines = [
        "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
    ]
    for row in table_rows:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(len(row))))
    return "\n".join(lines)


def format_show(result: CascadeShowResult) -> str:
    """Render one CascadeShowResult — header + contracts + audit log.

    Markdown-flavored plain text (operator-readable, also pipe-able
    into glow). Three sections so the operator scans top-down without
    losing the cascade id.
    """
    p = result.proposal
    lines: list[str] = []
    lines.append(f"# Cascade {p.cascade_id}")
    lines.append("")
    lines.append(f"- risk: {p.risk_id}")
    lines.append(f"- spike: {p.spike_ticket_id}")
    lines.append(f"- state: {p.state.value}")
    lines.append(f"- generated: {_ts(p.generated_at)}")
    if p.holding_for:
        lines.append(f"- holding for: {p.holding_for}")
    if p.constraint:
        lines.append(f"- constraint: {p.constraint}")
    if p.rejected_reason:
        lines.append(f"- rejected reason: {p.rejected_reason}")
    lines.append("")
    lines.append("## Finding")
    lines.append("")
    lines.append(p.finding)
    lines.append("")
    lines.append("## Contracts")
    lines.append("")
    if not p.contracts:
        lines.append("_(none)_")
    else:
        for c in p.contracts:
            lines.append(f"- `{c.uri}` — {c.proposed_disposition}")
    if p.stages:
        lines.append("")
        lines.append("## Stages")
        lines.append("")
        for s in p.stages:
            mark = "x" if s.approved else " "
            actor = f" by {s.approved_by}" if s.approved_by else ""
            lines.append(
                f"- [{mark}] `{s.stage_id}` ({len(s.contracts)} contract(s)){actor}"
            )
    lines.append("")
    lines.append("## Audit log")
    lines.append("")
    if not result.audit_entries:
        lines.append("_(none)_")
    else:
        for e in result.audit_entries:
            extras: list[str] = []
            if e.stage_id:
                extras.append(f"stage={e.stage_id}")
            if e.holding_for:
                extras.append(f"holding_for={e.holding_for}")
            if e.reason:
                extras.append(f"reason={e.reason}")
            extra = (" | " + ", ".join(extras)) if extras else ""
            lines.append(f"- {_ts(e.timestamp)} `{e.action}` by {e.actor}{extra}")
    return "\n".join(lines)


def format_audit_markdown(entries: list[CascadeAuditEntry]) -> str:
    """Render filtered audit entries as a markdown table.

    The cross-project analytics use case (rejection patterns) wants
    something pipeable into a viewer; markdown table is friendly for
    glow + git diffs. Empty entries → "_(no audit entries)_".
    """
    if not entries:
        return "_(no audit entries)_"
    lines = [
        "| timestamp | cascade | risk | action | actor | reason |",
        "|---|---|---|---|---|---|",
    ]
    for e in entries:
        reason = (e.reason or "").replace("|", "\\|")
        lines.append(
            f"| {_ts(e.timestamp)} | {e.cascade_id} | {e.risk_id} "
            f"| {e.action} | {e.actor} | {reason} |"
        )
    return "\n".join(lines)
