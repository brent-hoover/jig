"""Tests for jig.config.resolve_workflow (Phase 2 Task D).

Covers the full doc-17 resolution order:

1. ``explicit`` (validated against ``workflows.available``)
2. ``by_type.<wt>.default_by_size.<size>``
3. ``by_type.<wt>.available[0]`` if single-valued
4. ``default_by_size.<size>``
5. fallback ``"default"``
"""

from pathlib import Path

import pytest

from jig.config import (
    Config,
    WorkflowResolutionError,
    WorkflowsSection,
    WorkflowTypeEntry,
    resolve_workflow,
)
from jig.project import Project


def _cfg(tmp_path: Path, workflows: WorkflowsSection) -> Config:
    return Config(
        project=Project(id="p", name="p", path=str(tmp_path)),
        workflows=workflows,
    )


class TestExplicit:
    def test_explicit_allowed(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            WorkflowsSection(available=["standard", "hotfix"]),
        )
        assert (
            resolve_workflow(cfg, work_type="feature", size="m", explicit="hotfix")
            == "hotfix"
        )

    def test_explicit_rejected_when_not_available(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            WorkflowsSection(available=["standard"]),
        )
        with pytest.raises(WorkflowResolutionError, match="missing"):
            resolve_workflow(cfg, work_type="feature", size="m", explicit="missing")

    def test_explicit_accepted_when_available_empty(self, tmp_path: Path) -> None:
        """Empty ``available`` = no allow-list; any explicit name wins."""
        cfg = _cfg(tmp_path, WorkflowsSection())
        assert (
            resolve_workflow(cfg, work_type="feature", size="m", explicit="anything")
            == "anything"
        )


class TestByTypeDefault:
    def test_by_type_default_by_size_wins(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            WorkflowsSection(
                default_by_size={"m": "standard"},
                available=["standard", "feature-m"],
                by_type={
                    "feature": WorkflowTypeEntry(
                        default_by_size={"m": "feature-m"},
                        available=["feature-m", "standard"],
                    )
                },
            ),
        )
        # by_type override beats the project-wide default_by_size.
        assert resolve_workflow(cfg, work_type="feature", size="m") == "feature-m"

    def test_by_type_singleton_available_used(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            WorkflowsSection(
                available=["sole"],
                by_type={
                    "bugfix": WorkflowTypeEntry(available=["sole"]),
                },
            ),
        )
        assert resolve_workflow(cfg, work_type="bugfix", size="m") == "sole"

    def test_by_type_multi_available_falls_through(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            WorkflowsSection(
                default_by_size={"m": "standard"},
                available=["standard", "a", "b"],
                by_type={
                    "bugfix": WorkflowTypeEntry(available=["a", "b"]),
                },
            ),
        )
        # Multi-valued ``available`` without default_by_size falls through
        # to the project-wide default_by_size.
        assert resolve_workflow(cfg, work_type="bugfix", size="m") == "standard"


class TestProjectDefault:
    def test_default_by_size(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            WorkflowsSection(
                default_by_size={"xs": "hotfix", "m": "standard"},
                available=["hotfix", "standard"],
            ),
        )
        assert resolve_workflow(cfg, work_type="feature", size="xs") == "hotfix"
        assert resolve_workflow(cfg, work_type="feature", size="m") == "standard"


class TestFallback:
    def test_fallback_to_default(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, WorkflowsSection())
        assert resolve_workflow(cfg, work_type="feature", size="m") == "default"

    def test_unmapped_size_falls_through(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            WorkflowsSection(default_by_size={"m": "standard"}),
        )
        # Size xl isn't mapped anywhere → final fallback.
        assert resolve_workflow(cfg, work_type="feature", size="xl") == "default"

    def test_implicit_pick_not_in_available_falls_through(self, tmp_path: Path) -> None:
        """Config inconsistency: resolved name not in allow-list.

        Per docstring we fall through to the next step rather than raise
        — Phase 2F surfaces it as a catalog error separately.
        """
        cfg = _cfg(
            tmp_path,
            WorkflowsSection(
                default_by_size={"m": "ghost"},
                available=["standard"],
            ),
        )
        # "ghost" not allowed → falls through to "default".
        assert resolve_workflow(cfg, work_type="feature", size="m") == "default"
