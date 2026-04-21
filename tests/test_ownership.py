"""Tests for jig.ownership.resolve_owner (Phase 3 Task F)."""

from pathlib import Path

import pytest

from jig.config import (
    Config,
    OwnershipSection,
    RoleAssignment,
    RolesSection,
    SpecOwnership,
)
from jig.ownership import OwnerRouting, OwnershipError, resolve_owner
from jig.project import Project
from jig.work_types import WorkTypeSchema


def _cfg(
    tmp_path: Path,
    *,
    spec_ownership: dict[str, str] | None = None,
    extra_ownership: dict[str, str] | None = None,
    roles: dict[str, RoleAssignment] | None = None,
) -> Config:
    spec = SpecOwnership.model_validate(spec_ownership or {})
    own = OwnershipSection.model_validate(
        {"spec": spec.model_dump(), **(extra_ownership or {})}
    )
    role_section = RolesSection(
        po=(roles or {}).get("po"),
        sa=(roles or {}).get("sa"),
    )
    return Config(
        project=Project(id="p", name="p", path=str(tmp_path)),
        ownership=own,
        roles=role_section,
    )


def _schema(**ownership: str) -> WorkTypeSchema:
    return WorkTypeSchema(
        work_type="feature",
        required=list(ownership),
        ownership=dict(ownership),
    )


class TestSpecField:
    def test_config_override_wins(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            spec_ownership={"behaviors": "sa"},  # odd but explicit
            roles={"sa": RoleAssignment(assignment="human", human="alice")},
        )
        schema = _schema(behaviors="po", summary="po")
        route = resolve_owner(
            cfg, "ticket://spec.behaviors", work_type_schema=schema
        )
        assert route.role == "sa"
        assert route.assignee == "alice"

    def test_schema_fallback_when_config_silent(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            roles={"sa": RoleAssignment(assignment="human", human="bob")},
        )
        schema = _schema(design="sa")
        route = resolve_owner(
            cfg, "ticket://spec.design", work_type_schema=schema
        )
        assert route.role == "sa"
        assert route.assignee == "bob"

    def test_unknown_field_raises(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        schema = _schema(summary="po")
        with pytest.raises(OwnershipError, match="secret_field"):
            resolve_owner(
                cfg, "ticket://spec.secret_field", work_type_schema=schema
            )


class TestWholeSpec:
    def test_defaults_to_po(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            roles={"po": RoleAssignment(assignment="human", human="pam")},
        )
        route = resolve_owner(cfg, "ticket://spec")
        assert route.role == "po"
        assert route.assignee == "pam"


class TestProjectArtifact:
    def test_architecture_routes_to_sa(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            extra_ownership={"architecture": "sa"},
            roles={"sa": RoleAssignment(assignment="human", human="sara")},
        )
        route = resolve_owner(cfg, "project://architecture")
        assert route.role == "sa"
        assert route.assignee == "sara"

    def test_missing_key_raises(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        with pytest.raises(OwnershipError, match="roadmap_v2"):
            resolve_owner(cfg, "project://roadmap_v2")


class TestAssignmentStyles:
    def test_human_with_helper_returns_template(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            roles={
                "po": RoleAssignment(
                    assignment="human_with_helper",
                    human="alice@example.com",
                    helper_template="po-helper",
                ),
            },
        )
        route = resolve_owner(cfg, "ticket://spec")
        assert route.assignment == "human_with_helper"
        assert route.helper_template == "po-helper"
        assert route.assignee == "alice@example.com"

    def test_agent_assignment_has_no_assignee(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            roles={
                "po": RoleAssignment(
                    assignment="agent",
                    helper_template="po-agent",
                ),
            },
        )
        route = resolve_owner(cfg, "ticket://spec")
        assert route.assignment == "agent"
        assert route.assignee is None

    def test_unstaffed_role_flagged(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)  # no roles declared
        route = resolve_owner(cfg, "ticket://spec")
        assert route.assignment == "unstaffed"
        assert route.assignee is None


class TestErrors:
    def test_missing_scheme(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        with pytest.raises(OwnershipError, match="missing scheme"):
            resolve_owner(cfg, "spec.behaviors")

    def test_unknown_scheme(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        with pytest.raises(OwnershipError, match="unknown target scheme"):
            resolve_owner(cfg, "decision://DR-0001")

    def test_unsupported_ticket_body(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        with pytest.raises(OwnershipError, match="unsupported ticket"):
            resolve_owner(cfg, "ticket://description")


class TestReturnShape:
    def test_route_is_frozen_dataclass(self, tmp_path: Path) -> None:
        cfg = _cfg(
            tmp_path,
            roles={"po": RoleAssignment(assignment="human", human="p")},
        )
        route = resolve_owner(cfg, "ticket://spec")
        assert isinstance(route, OwnerRouting)
        with pytest.raises(Exception):
            route.role = "other"  # type: ignore[misc]
