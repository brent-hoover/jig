"""Auto-apply path for confidence-1.0 mechanical reviewer comments.

Per ``docs/v2.0/pm-workflow/design.md`` section "Auto-apply path": when a
mechanical reviewer comment carries ``confidence: 1.0`` AND populates
``suggested_diff``, the orchestrator applies the diff directly and
skips the dev-agent fix cycle for that comment. The dev agent's
review-fix loop is expensive (one full agent spawn per cycle); skipping
it for diffs the reviewer is certain about and willing to write
itself is the load-bearing latency win for the federation.

Skip rules per design:

- ``confidence < 1.0`` -- judgment, not deterministic; the operator
  should see this finding before any code lands.
- ``suggested_diff`` empty / missing -- nothing to apply.
- ``severity: critical`` -- the operator MUST see critical findings,
  even when the diff is mechanical and certain.

For MVP scope, this module is the reusable infrastructure; the
orchestrator hookup that calls ``apply_suggested_diffs`` after a
review pass is wired by the synthetic operator (and, in production,
by the future Coordinator's per-cycle fix pass). The bones
contract-compliance reviewer doesn't yet emit ``suggested_diff``;
populating that is incremental MVP+ work per reviewer.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from jig.reviewers.comment import ReviewerComment, Severity


class AutoApplyResult(BaseModel):
    """Per-batch outcome of an ``apply_suggested_diffs`` invocation.

    Three buckets -- applied, skipped, failed -- let the caller emit
    the right downstream events without re-classifying. ``failed``
    pairs the comment with a short reason category so analytics can
    slice the failure modes without parsing prose.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    applied: list[ReviewerComment] = []
    skipped: list[ReviewerComment] = []
    failed: list[tuple[ReviewerComment, str]] = []


# ---- skip predicates ------------------------------------------------------


def _should_skip(comment: ReviewerComment) -> str | None:
    """Return a short reason category if the comment should be skipped, else None."""
    if comment.confidence < 1.0:
        return "judgment_confidence"
    if not comment.suggested_diff or not comment.suggested_diff.strip():
        return "no_suggested_diff"
    # Severity is stored as the enum value (use_enum_values=True), so
    # comparison covers both string and enum cases.
    if comment.severity in (Severity.CRITICAL, Severity.CRITICAL.value):
        return "critical_severity"
    return None


# ---- git runner -----------------------------------------------------------


async def _run_git(
    worktree_path: Path, *args: str, stdin: bytes | None = None
) -> tuple[int, str]:
    """Run a git subcommand and return (returncode, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=worktree_path,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate(stdin)
    return proc.returncode, stderr.decode("utf-8", errors="replace").strip()


def _commit_message(comment: ReviewerComment) -> str:
    """Compose the auto-apply commit message per design.

    Format: ``chore(auto-apply): <comment.type> at <file>:<line>`` with
    omissions when file/line aren't populated.
    """
    suffix_parts: list[str] = []
    if comment.file:
        loc = comment.file
        if comment.line:
            loc = f"{loc}:{comment.line}"
        suffix_parts.append(f"at {loc}")
    suffix = " " + " ".join(suffix_parts) if suffix_parts else ""
    return f"chore(auto-apply): {comment.type}{suffix}"


# ---- public entry ---------------------------------------------------------


async def apply_suggested_diffs(
    comments: list[ReviewerComment],
    worktree_path: Path,
) -> AutoApplyResult:
    """Apply confidence-1.0 mechanical comments' suggested diffs.

    Iterates comments in order. Each non-skip comment runs ``git apply``
    against the worktree; on success a chore commit lands so the next
    comment's diff applies cleanly on top. On failure the comment moves
    to ``failed`` with a short category and the loop continues -- one
    bad patch shouldn't block the rest of the batch.
    """
    result = AutoApplyResult()
    for comment in comments:
        skip_reason = _should_skip(comment)
        if skip_reason is not None:
            result.skipped.append(comment)
            continue

        diff = comment.suggested_diff or ""
        rc, stderr = await _run_git(
            worktree_path,
            "apply",
            "--whitespace=nowarn",
            "-",
            stdin=diff.encode("utf-8"),
        )
        if rc != 0:
            result.failed.append(
                (comment, f"git_apply_rc={rc}: {stderr[:200]}")
            )
            continue

        rc, _ = await _run_git(worktree_path, "add", "-A")
        if rc != 0:
            result.failed.append((comment, f"git_add_rc={rc}"))
            continue

        rc, commit_err = await _run_git(
            worktree_path, "commit", "-m", _commit_message(comment)
        )
        if rc != 0:
            result.failed.append(
                (comment, f"git_commit_rc={rc}: {commit_err[:200]}")
            )
            continue

        result.applied.append(comment)

    return result


__all__ = [
    "AutoApplyResult",
    "apply_suggested_diffs",
]
