"""AI-audit taxonomy: the single source mapping each defect pattern to its
detection (ruff rule code or judgment) and the federation reviewer that owns it.

The manifest ships at ``jig/code_quality/taxonomy.yaml``. See
``feature-work/code-quality-system/design.md`` for the full story.
"""

from __future__ import annotations

from functools import cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


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
