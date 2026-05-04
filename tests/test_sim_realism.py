"""Realism-budget logging (Track H MVP follow-on).

Tests RealismGap model + JSONL store round-trip + the
``jig sim realism log/list`` CLI surface.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from jig.sim.cli import sim
from jig.sim.realism import (
    REALISM_GAPS_RELPATH,
    RealismGap,
    RealismGapsStore,
    list_gaps,
    log_gap,
)


# ---- model ---------------------------------------------------------------


def test_realism_gap_minimal_fields():
    gap = RealismGap(kind="ambiguous-confirmation", description="x")
    assert gap.kind == "ambiguous-confirmation"
    assert gap.source == "operator"
    assert gap.persona_to_extend is None
    # observed_at defaults to "now" — within the last few seconds.
    assert (datetime.now(timezone.utc) - gap.observed_at).total_seconds() < 5


def test_realism_gap_rejects_blank_kind():
    with pytest.raises(ValidationError):
        RealismGap(kind="", description="x")


def test_realism_gap_rejects_blank_description():
    with pytest.raises(ValidationError):
        RealismGap(kind="x", description="")


def test_realism_gap_rejects_invalid_source():
    with pytest.raises(ValidationError):
        RealismGap(kind="x", description="y", source="made-up")  # type: ignore[arg-type]


def test_realism_gap_rejects_extra_fields():
    """``extra='forbid'`` catches typos."""
    with pytest.raises(ValidationError):
        RealismGap.model_validate(
            {
                "kind": "x",
                "description": "y",
                "typo_field": "boom",
            }
        )


# ---- store round-trip ----------------------------------------------------


@pytest.mark.asyncio
async def test_log_gap_writes_to_canonical_path(tmp_path: Path):
    gap = RealismGap(kind="x", description="y")
    await log_gap(tmp_path, gap)
    assert (tmp_path / REALISM_GAPS_RELPATH).is_file()


@pytest.mark.asyncio
async def test_list_gaps_round_trips_through_jsonl(tmp_path: Path):
    g1 = RealismGap(kind="a", description="alpha")
    g2 = RealismGap(
        kind="b",
        description="beta",
        source="real-run",
        persona_to_extend="ambivalent",
    )
    await log_gap(tmp_path, g1)
    await log_gap(tmp_path, g2)
    rows = await list_gaps(tmp_path)
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["a", "b"]
    found_b = next(r for r in rows if r.kind == "b")
    assert found_b.persona_to_extend == "ambivalent"
    assert found_b.source == "real-run"


@pytest.mark.asyncio
async def test_list_gaps_empty_when_no_logs(tmp_path: Path):
    rows = await list_gaps(tmp_path)
    assert rows == []


@pytest.mark.asyncio
async def test_list_gaps_filters_since(tmp_path: Path):
    """``since=`` returns only gaps observed at-or-after the threshold."""
    old = RealismGap(
        kind="old",
        description="old",
        observed_at=datetime.now(timezone.utc) - timedelta(days=7),
    )
    fresh = RealismGap(kind="fresh", description="fresh")
    await log_gap(tmp_path, old)
    await log_gap(tmp_path, fresh)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = await list_gaps(tmp_path, since=cutoff)
    assert [r.kind for r in rows] == ["fresh"]


@pytest.mark.asyncio
async def test_realism_gaps_store_reuses_loaded_state(tmp_path: Path):
    """A long-lived store doesn't reload on every call."""
    store = RealismGapsStore(tmp_path)
    await store.log_gap(RealismGap(kind="a", description="x"))
    await store.log_gap(RealismGap(kind="b", description="y"))
    rows = await store.list_gaps()
    assert len(rows) == 2


# ---- CLI -----------------------------------------------------------------


def test_cli_realism_log_writes_gap(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        sim,
        [
            "realism", "log",
            "Operator confirmed playback with bare yeah; meant 'I dunno'.",
            "--kind", "ambiguous-confirmation",
            "--persona", "ambivalent",
            "--project-root", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "logged gap" in result.output
    assert (tmp_path / REALISM_GAPS_RELPATH).is_file()


def test_cli_realism_log_then_list_round_trips(tmp_path: Path):
    runner = CliRunner()
    log_result = runner.invoke(
        sim,
        [
            "realism", "log", "first one",
            "--kind", "k1",
            "--project-root", str(tmp_path),
        ],
    )
    assert log_result.exit_code == 0, log_result.output
    list_result = runner.invoke(
        sim,
        ["realism", "list", "--project-root", str(tmp_path)],
    )
    assert list_result.exit_code == 0, list_result.output
    assert "first one" in list_result.output
    assert "k1" in list_result.output


def test_cli_realism_list_empty_message(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        sim, ["realism", "list", "--project-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "no realism gaps logged" in result.output


def test_cli_realism_log_default_source_operator(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(
        sim,
        [
            "realism", "log", "x",
            "--project-root", str(tmp_path),
        ],
    )
    list_result = runner.invoke(
        sim, ["realism", "list", "--project-root", str(tmp_path)]
    )
    assert "operator/general" in list_result.output


def test_cli_realism_log_real_run_source(tmp_path: Path):
    runner = CliRunner()
    runner.invoke(
        sim,
        [
            "realism", "log", "x",
            "--source", "real-run",
            "--project-root", str(tmp_path),
        ],
    )
    list_result = runner.invoke(
        sim, ["realism", "list", "--project-root", str(tmp_path)]
    )
    assert "real-run" in list_result.output
