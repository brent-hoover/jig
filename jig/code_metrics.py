"""Deterministic code-quality signal over the files a change touched.

The objective half of the quality picture — cyclomatic complexity (radon),
ruff finding count, and a non-blank/non-comment LoC delta — computed straight
from tool output with no LLM in the loop. Surfaced as a signal (logged at
commit time, injected into reviewer prompts); it never gates a commit.

``count_loc`` and ``max_cyclomatic`` are the canonical definitions reused by
the prompt-style eval harness, which imports them from here so there is exactly
one radon call site in the codebase.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, computed_field
from radon.complexity import cc_visit

from jig.code_quality.taxonomy import TaxonomyHit, scan_taxonomy

_logger = logging.getLogger(__name__)

# McCabe's classic ceiling and the radon B/C rank boundary. At or below this a
# function is "moderate"; strictly above it, we flag (never block).
CC_FLAG_THRESHOLD = 10


def count_loc(code: str) -> int:
    """Non-blank, non-comment lines.

    A bare comment doesn't count; a line of code with a trailing comment does.
    """
    n = 0
    for raw in code.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        n += 1
    return n


def cc_with_location(code: str) -> tuple[int, str | None]:
    """Largest cyclomatic complexity in ``code`` and the function that hit it.

    Returns ``(0, None)`` for code with no functions/methods or that doesn't
    parse — a syntax error is surfaced elsewhere (failing tests, ruff), not here.
    """
    try:
        blocks = cc_visit(code)
    except SyntaxError:
        return 0, None
    if not blocks:
        return 0, None
    top = max(blocks, key=lambda block: block.complexity)
    return top.complexity, top.name


def max_cyclomatic(code: str) -> int:
    """Largest cyclomatic complexity across all functions/methods (0 if none)."""
    return cc_with_location(code)[0]


class ChangeMetrics(BaseModel):
    """Objective metrics over the Python files a change touched.

    Immutable value object. ``flagged`` is derived from ``max_cc`` rather than
    stored, so it can never disagree with the threshold.
    """

    model_config = ConfigDict(frozen=True)

    max_cc: int = Field(ge=0)
    max_cc_location: str | None = None
    ruff_findings: int = Field(ge=0)
    loc_delta: int
    taxonomy_hits: tuple[TaxonomyHit, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def flagged(self) -> bool:
        return self.max_cc > CC_FLAG_THRESHOLD


_EMPTY = ChangeMetrics(max_cc=0, max_cc_location=None, ruff_findings=0, loc_delta=0)


async def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    """Run ``cmd`` with a fixed argv (no shell), returning ``(rc, stdout_text)``."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, _ = await proc.communicate()
    assert proc.returncode is not None
    return proc.returncode, out.decode("utf-8", errors="replace")


async def _changed_py_files(worktree_path: Path, base_ref: str) -> set[str]:
    """Union of changed ``.py`` files: committed-since-base, staged, unstaged,
    untracked. Each git query is independent — an unresolvable ``base_ref`` just
    drops the committed set and leaves the working-tree sets intact."""
    files: set[str] = set()
    queries = (
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "diff", "--name-only", "--cached"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    for cmd in queries:
        rc, out = await _run(cmd, worktree_path)
        if rc == 0:
            files.update(out.split())
    return {f for f in files if f.endswith(".py")}


async def _file_at_ref(worktree_path: Path, ref: str, rel_path: str) -> str:
    """Contents of ``rel_path`` at ``ref``; empty string if absent there."""
    rc, out = await _run(["git", "show", f"{ref}:{rel_path}"], worktree_path)
    return out if rc == 0 else ""


async def _count_ruff_findings(worktree_path: Path, rel_paths: list[str]) -> int:
    """Ruff findings across ``rel_paths``. Ruff exits non-zero when findings are
    present — that's data, not failure — so the return code is ignored."""
    if not rel_paths:
        return 0
    _, out = await _run(
        ["ruff", "check", "--output-format=json", *rel_paths], worktree_path
    )
    raw = out.strip()
    if not raw:
        return 0
    try:
        return len(json.loads(raw))
    except json.JSONDecodeError:
        _logger.warning("code_metrics: ruff did not emit JSON; reporting 0 findings")
        return 0


async def compute_change_metrics(
    worktree_path: Path,
    base_ref: str = "main",
) -> ChangeMetrics:
    """Compute :class:`ChangeMetrics` for the Python files changed in a worktree.

    Resolves the changed-file set (committed-since-``base_ref`` plus working-tree
    changes — the commit path calls this *before* ``git add -A``), then computes
    max cyclomatic complexity + location, ruff finding count, and the net
    non-blank/non-comment LoC delta against ``base_ref``.

    Signal only: per the feature's problem constraint a metrics failure must
    never block a commit, so any tooling/IO error degrades to empty metrics and
    a logged warning rather than propagating.
    """
    worktree_path = Path(worktree_path)
    try:
        changed = await _changed_py_files(worktree_path, base_ref)
        if not changed:
            return _EMPTY

        max_cc = 0
        max_cc_location: str | None = None
        loc_delta = 0
        on_disk: list[str] = []

        for rel in sorted(changed):
            abs_path = worktree_path / rel
            present = abs_path.is_file()
            after = (
                abs_path.read_text(encoding="utf-8", errors="replace")
                if present
                else ""
            )
            if present:
                on_disk.append(rel)
            before = await _file_at_ref(worktree_path, base_ref, rel)
            loc_delta += count_loc(after) - count_loc(before)
            cc, name = cc_with_location(after)
            if cc > max_cc:
                max_cc = cc
                max_cc_location = f"{rel}:{name}" if name else rel

        ruff_findings = await _count_ruff_findings(worktree_path, on_disk)
        # ``scan_taxonomy`` uses a synchronous ``subprocess.run`` (kept sync so
        # tests can call it directly); offload it to a thread so we don't block
        # the event loop while ruff runs.
        loop = asyncio.get_running_loop()
        taxonomy_hits = tuple(
            await loop.run_in_executor(
                None,
                scan_taxonomy,
                worktree_path,
                [worktree_path / rel for rel in on_disk],
            )
        )
        return ChangeMetrics(
            max_cc=max_cc,
            max_cc_location=max_cc_location,
            ruff_findings=ruff_findings,
            loc_delta=loc_delta,
            taxonomy_hits=taxonomy_hits,
        )
    except (OSError, ValueError):
        _logger.warning(
            "code_metrics: failed to compute change metrics for %s; reporting empty",
            worktree_path,
            exc_info=True,
        )
        return _EMPTY
