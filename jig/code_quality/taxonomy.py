"""AI-audit taxonomy: the single source mapping each defect pattern to its
detection (ruff rule code or judgment) and the federation reviewer that owns it.

The manifest ships at ``jig/code_quality/taxonomy.yaml``. See
``feature-work/code-quality-system/design.md`` for the full story.
"""

from __future__ import annotations

import json
import logging
import subprocess
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

_logger = logging.getLogger(__name__)


class Detection(BaseModel):
    """How a taxonomy pattern is detected: a ruff rule code, or judgment-only."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["ruff", "judgment"]
    ref: str | None = None


class TaxonomyEntry(BaseModel):
    """One catalogued AI-defect pattern."""

    model_config = ConfigDict(frozen=True)

    id: str
    category: str
    title: str
    detection: Detection
    owning_reviewer: str
    cue: str


@cache
def load_taxonomy() -> tuple[TaxonomyEntry, ...]:
    """Load the shipped taxonomy manifest (cached for the process lifetime)."""
    text = resources.files("jig.code_quality").joinpath("taxonomy.yaml").read_text()
    raw = yaml.safe_load(text)
    return tuple(TaxonomyEntry.model_validate(item) for item in raw)


def taxonomy_ruff_select() -> list[str]:
    """The curated ruff ``--select`` list: every ruff rule the taxonomy maps to."""
    return sorted(
        {
            e.detection.ref
            for e in load_taxonomy()
            if e.detection.kind == "ruff" and e.detection.ref
        }
    )


class TaxonomyHit(BaseModel):
    """One concrete deterministic detection of a taxonomy pattern in changed code."""

    model_config = ConfigDict(frozen=True)

    id: str
    category: str
    file: str
    line: int
    reviewer: str


def scan_taxonomy(worktree_path: Path, py_files: list[Path]) -> list[TaxonomyHit]:
    """Run the curated taxonomy ruff pass over ``py_files`` and map findings to hits.

    Uses ruff's JSON output and the manifest's ruff entries to translate each
    finding's ``code`` to a :class:`TaxonomyHit`. Signal-only — a ruff/tooling
    failure (missing binary, malformed output) degrades to ``[]`` with a logged
    warning rather than raising, matching the contract of ``compute_change_metrics``.
    """
    files = [str(p) for p in py_files if p.suffix == ".py" and p.is_file()]
    if not files:
        return []
    select = taxonomy_ruff_select()
    if not select:
        return []
    by_code = {
        e.detection.ref: e for e in load_taxonomy() if e.detection.kind == "ruff"
    }
    try:
        proc = subprocess.run(
            [
                "ruff",
                "check",
                "--select",
                ",".join(select),
                "--output-format=json",
                *files,
            ],
            cwd=worktree_path,
            capture_output=True,
            text=True,
        )
        raw = proc.stdout.strip()
        findings = json.loads(raw) if raw else []
    except (OSError, ValueError):
        _logger.warning(
            "taxonomy scan failed for %s; reporting no hits",
            worktree_path,
            exc_info=True,
        )
        return []
    hits: list[TaxonomyHit] = []
    for fnd in findings:
        entry = by_code.get(fnd.get("code"))
        if entry is None:
            continue
        loc = fnd.get("location", {})
        hits.append(
            TaxonomyHit(
                id=entry.id,
                category=entry.category,
                file=fnd.get("filename", ""),
                line=int(loc.get("row", 0) or 0),
                reviewer=entry.owning_reviewer,
            )
        )
    return hits
