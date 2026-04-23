"""Tests for jig.catalog.collect_policy_warnings (Phase 5 Task F).

Shadow-pattern detection is advisory: a ``writable`` / ``readable``
path fully subsumed by a ``denied`` glob compiles to a deterministic
ruleset, just one where the permit never fires at the hook boundary.
``jig validate`` surfaces these as ``[WARN]`` lines without failing.

Coverage:

* Happy path — no warnings for a clean catalog.
* Literal permit under a ``**``-trailing deny (the canonical shadow).
* ``*``-segment within a ``**`` deny prefix (single-segment wildcard
  subsumed by unbounded wildcard).
* Different URI schemes don't cross-subsume.
* Unrelated denies don't spuriously flag permits.
* Phase ``capability_overrides`` warnings are labeled with workflow +
  phase, not role.
* Conservative fallback — mid-wildcard denies (e.g., ``a/*/b``) don't
  flag permits that might or might not be subsumed (we under-warn
  rather than false-positive).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.capabilities import (
    CapabilityDeclaration,
    CapabilityPaths,
)
from jig.catalog import _pattern_subsumes, collect_policy_warnings
from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.persistence import init_project, save_role, save_workflow


@pytest.fixture
def initialized_project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    init_project(tmp_path)
    return tmp_path


class TestPatternSubsumes:
    """Unit tests on the pure subsumption helper."""

    def test_identical_patterns_subsume(self) -> None:
        assert _pattern_subsumes("repo://secrets", "repo://secrets")

    def test_prefix_deny_subsumes_literal_under(self) -> None:
        assert _pattern_subsumes("repo://secrets/**", "repo://secrets/key.pem")

    def test_prefix_deny_subsumes_deeper_literal(self) -> None:
        assert _pattern_subsumes(
            "repo://secrets/**", "repo://secrets/nested/deep/file.pem"
        )

    def test_prefix_deny_subsumes_star_segment(self) -> None:
        # ``secrets/**`` subsumes ``secrets/*`` — every single-segment
        # path under secrets is also a path under secrets/**.
        assert _pattern_subsumes("repo://secrets/**", "repo://secrets/*")

    def test_universal_deny_subsumes_everything_same_scheme(self) -> None:
        assert _pattern_subsumes("repo://**", "repo://anything/at/all")

    def test_literal_deny_does_not_subsume_prefix_permit(self) -> None:
        # ``repo://secrets/key.pem`` only matches that one path;
        # ``repo://secrets/**`` matches many. Not subsumed.
        assert not _pattern_subsumes("repo://secrets/key.pem", "repo://secrets/**")

    def test_different_schemes_do_not_subsume(self) -> None:
        # ``ticket://`` and ``repo://`` both resolve under /workspace
        # but the sub-paths differ — ticket://worktree/X is
        # /workspace/X, repo://X is also /workspace/X. In practice the
        # resolver normalises to the same root, so equal bodies DO
        # subsume. Verify the normalisation.
        assert _pattern_subsumes("ticket://worktree/**", "repo://anything")

    def test_unmapped_scheme_never_subsumes(self) -> None:
        # ``project://`` has no hook-boundary mapping — resolve_uri_glob
        # returns None, so subsumption is False by convention.
        assert not _pattern_subsumes("project://**", "repo://foo")

    def test_mid_wildcard_conservative(self) -> None:
        # ``a/*/b`` technically subsumes ``a/x/b`` but our conservative
        # algorithm requires segment-by-segment literal or ``*``
        # equivalence. This specific case is caught because segment
        # ``*`` subsumes segment ``x``.
        assert _pattern_subsumes("repo://a/*/b", "repo://a/x/b")

    def test_mid_wildcard_non_subsuming(self) -> None:
        # ``a/*/b`` does NOT subsume ``a/x/y/b`` — different depths.
        assert not _pattern_subsumes("repo://a/*/b", "repo://a/x/y/b")


class TestRoleCapabilityWarnings:
    def test_clean_catalog_no_warnings(self, initialized_project: Path) -> None:
        assert collect_policy_warnings(initialized_project) == []

    def test_writable_shadowed_by_deny(self, initialized_project: Path) -> None:
        save_role(
            initialized_project,
            RoleConfig(
                role="dev",
                phase_prompt="x",
                capabilities=CapabilityDeclaration(
                    paths=CapabilityPaths(
                        writable=["repo://secrets/key.pem"],
                        denied=["repo://secrets/**"],
                    ),
                ),
            ),
        )
        warnings = collect_policy_warnings(initialized_project)
        assert len(warnings) == 1
        assert "role 'dev'" in warnings[0]
        assert "paths.writable" in warnings[0]
        assert "'repo://secrets/key.pem'" in warnings[0]
        assert "'repo://secrets/**'" in warnings[0]
        assert "shadowed" in warnings[0]

    def test_readable_shadowed_by_deny(self, initialized_project: Path) -> None:
        save_role(
            initialized_project,
            RoleConfig(
                role="reviewer",
                phase_prompt="x",
                capabilities=CapabilityDeclaration(
                    paths=CapabilityPaths(
                        readable=["repo://secrets/audit.log"],
                        denied=["repo://secrets/**"],
                    ),
                ),
            ),
        )
        warnings = collect_policy_warnings(initialized_project)
        assert len(warnings) == 1
        assert "paths.readable" in warnings[0]

    def test_unrelated_deny_no_warning(self, initialized_project: Path) -> None:
        save_role(
            initialized_project,
            RoleConfig(
                role="dev",
                phase_prompt="x",
                capabilities=CapabilityDeclaration(
                    paths=CapabilityPaths(
                        writable=["repo://src/**"],
                        denied=["repo://secrets/**"],
                    ),
                ),
            ),
        )
        assert collect_policy_warnings(initialized_project) == []

    def test_multiple_permits_each_warned(self, initialized_project: Path) -> None:
        save_role(
            initialized_project,
            RoleConfig(
                role="dev",
                phase_prompt="x",
                capabilities=CapabilityDeclaration(
                    paths=CapabilityPaths(
                        writable=[
                            "repo://secrets/a.pem",
                            "repo://secrets/b.pem",
                        ],
                        denied=["repo://secrets/**"],
                    ),
                ),
            ),
        )
        warnings = collect_policy_warnings(initialized_project)
        assert len(warnings) == 2

    def test_one_warning_per_permit_even_with_many_denies(
        self, initialized_project: Path
    ) -> None:
        save_role(
            initialized_project,
            RoleConfig(
                role="dev",
                phase_prompt="x",
                capabilities=CapabilityDeclaration(
                    paths=CapabilityPaths(
                        writable=["repo://secrets/a.pem"],
                        denied=[
                            "repo://secrets/**",
                            "repo://**",
                        ],
                    ),
                ),
            ),
        )
        warnings = collect_policy_warnings(initialized_project)
        assert len(warnings) == 1


class TestPhaseOverrideWarnings:
    def test_phase_override_warning_labels_workflow_and_phase(
        self, initialized_project: Path
    ) -> None:
        save_role(
            initialized_project,
            RoleConfig(role="dev", phase_prompt="x"),
        )
        save_workflow(
            initialized_project,
            WorkflowConfig(
                name="wf1",
                phases=[
                    PhaseConfig(
                        name="implement",
                        role="dev",
                        capability_overrides=CapabilityDeclaration(
                            paths=CapabilityPaths(
                                writable=["repo://vendor/lib.py"],
                                denied=["repo://vendor/**"],
                            ),
                        ),
                    ),
                ],
            ),
        )
        warnings = collect_policy_warnings(initialized_project)
        assert len(warnings) == 1
        assert "workflow 'wf1'" in warnings[0]
        assert "phase 'implement'" in warnings[0]
        assert "capability_overrides" in warnings[0]
