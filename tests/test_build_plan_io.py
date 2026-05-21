"""Build-plan path helpers + writer (Track F1, bones).

Bones scope: a Pydantic ``BuildPlan`` round-trips through
``write_build_plan`` / ``load_build_plan``. No PM agent yet — the
synthetic operator (Track H) calls these helpers directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.intent import Intent
from jig.schemas.plan import (
    BuildPlan,
    Epic,
    EpicLayers,
    LayerStatus,
    LayerStatusEnum,
    OrderingRule,
)
from jig.spec_loader import (
    build_plan_path,
    load_build_plan,
    write_build_plan,
)
from tests._test_ticket import EPIC_AC_PLACEHOLDER_BULLET


def _intent() -> Intent:
    return Intent(problem="Demo bones spine", simplest_solution="One ticket")


def _bones_plan() -> BuildPlan:
    return BuildPlan(
        project="bones-demo",
        epics=[
            Epic(
                id="catalog-ingest",
                title="Catalog ingest",
                suite="catalog",
                modules=["catalog-ingest"],
                layers=EpicLayers(
                    bones=LayerStatus(tickets=["tb-catalog-ingest"]),
                ),
                risks_addressed=["r-shopify-delta"],
                intent=_intent(),
                acceptance_criteria=[EPIC_AC_PLACEHOLDER_BULLET],
            )
        ],
    )


# ---- path helpers --------------------------------------------------------


def test_build_plan_path_helper(tmp_path: Path):
    p = build_plan_path(tmp_path)
    assert p == tmp_path / ".jig" / "plan" / "build-plan.yaml"


# ---- load: absence -------------------------------------------------------


def test_load_build_plan_missing_raises(tmp_path: Path):
    """Bones treats absence as 'no plan yet' — caller decides via try/except."""
    with pytest.raises(FileNotFoundError):
        load_build_plan(tmp_path)


# ---- writer happy path ---------------------------------------------------


def test_write_build_plan_creates_file(tmp_path: Path):
    plan = _bones_plan()
    write_build_plan(tmp_path, plan)
    assert build_plan_path(tmp_path).is_file()


def test_write_build_plan_roundtrips(tmp_path: Path):
    plan = _bones_plan()
    write_build_plan(tmp_path, plan)
    loaded = load_build_plan(tmp_path)
    assert loaded == plan


def test_write_build_plan_preserves_top_level_key_order(tmp_path: Path):
    """Deterministic ordering — diffs across writes stay readable.

    ``yaml.safe_dump(..., sort_keys=False)`` emits keys in the order
    Pydantic dumps them (declaration order). Spot-check that
    ``project`` precedes ``epics`` so the synthetic operator's golden
    assertions don't depend on alphabetical drift.
    """
    write_build_plan(tmp_path, _bones_plan())
    text = build_plan_path(tmp_path).read_text()
    assert text.index("project:") < text.index("epics:")


def test_write_build_plan_atomic_overwrite(tmp_path: Path):
    """Repeated writes replace the file rather than append."""
    write_build_plan(tmp_path, _bones_plan())
    second = _bones_plan()
    second.epics[0].layers.bones.status = LayerStatusEnum.IN_PROGRESS
    write_build_plan(tmp_path, second)
    loaded = load_build_plan(tmp_path)
    assert loaded.epics[0].layers.bones.status == LayerStatusEnum.IN_PROGRESS


def test_write_build_plan_creates_parent_dir(tmp_path: Path):
    """``.jig/plan/`` may not exist yet on a fresh project."""
    assert not (tmp_path / ".jig" / "plan").exists()
    write_build_plan(tmp_path, _bones_plan())
    assert (tmp_path / ".jig" / "plan").is_dir()


def test_load_build_plan_validates_schema(tmp_path: Path):
    """A hand-edited plan that fails schema validation surfaces clearly."""
    path = build_plan_path(tmp_path)
    path.parent.mkdir(parents=True)
    # Missing required ``project`` key.
    path.write_text(yaml.safe_dump({"spec_version": 1, "epics": []}))
    with pytest.raises(Exception):  # ValidationError
        load_build_plan(tmp_path)


def test_write_build_plan_default_ordering_rule(tmp_path: Path):
    """Bones default is bones_first; the writer emits the enum value."""
    write_build_plan(tmp_path, _bones_plan())
    raw = yaml.safe_load(build_plan_path(tmp_path).read_text())
    assert raw["ordering_rule"] == OrderingRule.BONES_FIRST.value
