"""Tests for jig.checks — check catalog shape loader (Phase 2 Task E).

The catalog parses to a discriminated union of three flavors
(``scripted`` / ``implementation_aware_agent`` / ``black_box_agent``).
We don't execute anything here — that's Phase 5.
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from jig.checks import (
    BlackBoxAgentCheck,
    CheckCatalog,
    CheckSeverity,
    ImplementationAwareAgentCheck,
    ScriptedCheck,
    load_check_catalog,
)


def _write_checks(project_path: Path, checks: dict) -> None:
    (project_path / ".jig").mkdir(exist_ok=True)
    (project_path / ".jig" / "checks.yaml").write_text(
        yaml.safe_dump({"checks": checks})
    )


class TestLoadingShape:
    def test_missing_file_returns_empty_catalog(self, tmp_path: Path) -> None:
        cat = load_check_catalog(tmp_path)
        assert cat.names() == []

    def test_empty_checks_mapping(self, tmp_path: Path) -> None:
        _write_checks(tmp_path, {})
        cat = load_check_catalog(tmp_path)
        assert cat.names() == []

    def test_checks_null_normalizes(self, tmp_path: Path) -> None:
        (tmp_path / ".jig").mkdir()
        (tmp_path / ".jig" / "checks.yaml").write_text("checks: null\n")
        cat = load_check_catalog(tmp_path)
        assert cat.names() == []

    def test_file_empty_normalizes(self, tmp_path: Path) -> None:
        (tmp_path / ".jig").mkdir()
        (tmp_path / ".jig" / "checks.yaml").write_text("")
        cat = load_check_catalog(tmp_path)
        assert cat.names() == []


class TestScriptedCheck:
    def test_minimal(self, tmp_path: Path) -> None:
        _write_checks(
            tmp_path,
            {"lint": {"type": "scripted", "command": "ruff check ."}},
        )
        cat = load_check_catalog(tmp_path)
        check = cat.get("lint")
        assert isinstance(check, ScriptedCheck)
        assert check.command == "ruff check ."
        assert check.working_dir == "."
        # Defaults from _CheckBase.
        assert check.severity == CheckSeverity.REQUIRED
        assert check.timeout_s == 600

    def test_severity_parses(self, tmp_path: Path) -> None:
        _write_checks(
            tmp_path,
            {
                "typecheck": {
                    "type": "scripted",
                    "command": "mypy .",
                    "severity": "warning",
                    "timeout_s": 120,
                }
            },
        )
        cat = load_check_catalog(tmp_path)
        check = cat.get("typecheck")
        assert isinstance(check, ScriptedCheck)
        assert check.severity == CheckSeverity.WARNING
        assert check.timeout_s == 120


class TestImplementationAwareAgent:
    def test_basic(self, tmp_path: Path) -> None:
        _write_checks(
            tmp_path,
            {
                "code-review": {
                    "type": "implementation_aware_agent",
                    "template": "Review the diff.",
                    "context": ["ticket://design"],
                    "max_tokens": 30_000,
                }
            },
        )
        cat = load_check_catalog(tmp_path)
        check = cat.get("code-review")
        assert isinstance(check, ImplementationAwareAgentCheck)
        assert check.template == "Review the diff."
        assert check.max_tokens == 30_000
        assert check.context == ["ticket://design"]


class TestBlackBoxAgent:
    def test_basic(self, tmp_path: Path) -> None:
        _write_checks(
            tmp_path,
            {
                "spec-conformance": {
                    "type": "black_box_agent",
                    "template": "Test against spec.",
                    "context": ["ticket://description"],
                    "excluded": ["src/**"],
                }
            },
        )
        cat = load_check_catalog(tmp_path)
        check = cat.get("spec-conformance")
        assert isinstance(check, BlackBoxAgentCheck)
        assert check.excluded == ["src/**"]
        # Different default max_tokens per doc 10.
        assert check.max_tokens == 80_000


class TestValidation:
    def test_unknown_type_raises(self, tmp_path: Path) -> None:
        _write_checks(
            tmp_path,
            {"broken": {"type": "telepathic", "command": "wish"}},
        )
        with pytest.raises(ValidationError):
            load_check_catalog(tmp_path)

    def test_scripted_missing_command_raises(self, tmp_path: Path) -> None:
        _write_checks(
            tmp_path,
            {"broken": {"type": "scripted"}},
        )
        with pytest.raises(ValidationError):
            load_check_catalog(tmp_path)

    def test_agent_missing_template_raises(self, tmp_path: Path) -> None:
        _write_checks(
            tmp_path,
            {"broken": {"type": "implementation_aware_agent"}},
        )
        with pytest.raises(ValidationError):
            load_check_catalog(tmp_path)


class TestCatalogAPI:
    def test_names_sorted(self, tmp_path: Path) -> None:
        _write_checks(
            tmp_path,
            {
                "zzz": {"type": "scripted", "command": "z"},
                "aaa": {"type": "scripted", "command": "a"},
                "mmm": {"type": "scripted", "command": "m"},
            },
        )
        cat = load_check_catalog(tmp_path)
        assert cat.names() == ["aaa", "mmm", "zzz"]

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        _write_checks(tmp_path, {"a": {"type": "scripted", "command": "x"}})
        cat = load_check_catalog(tmp_path)
        assert cat.get("nope") is None

    def test_direct_catalog_construction(self) -> None:
        cat = CheckCatalog.model_validate(
            {"lint": {"type": "scripted", "command": "x"}}
        )
        assert cat.names() == ["lint"]
