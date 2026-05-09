"""Tests for canonicalize config loaders."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.canonicalize import (
    EscalationConfig,
    EscalationRoute,
    Formatter,
    FormattersConfig,
    load_escalation_config,
    load_formatters,
    resolve_route,
)


class TestLoadFormatters:
    def test_missing_returns_empty(self, tmp_path: Path) -> None:
        cfg = load_formatters(tmp_path)
        assert isinstance(cfg, FormattersConfig)
        assert cfg.formatters == []

    def test_present(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".jig" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "formatters.yml").write_text(
            "formatters:\n"
            "  - id: ruff-format\n"
            "    cmd: 'ruff format {files}'\n"
            "    files: ['*.py']\n"
            "  - id: prettier\n"
            "    cmd: 'prettier --write {files}'\n"
            "    files: ['*.ts', '*.tsx']\n"
            "    autofix: false\n"
        )
        cfg = load_formatters(tmp_path)
        assert len(cfg.formatters) == 2
        assert cfg.formatters[0].id == "ruff-format"
        assert cfg.formatters[0].autofix is True
        assert cfg.formatters[1].autofix is False
        assert cfg.formatters[1].files == ["*.ts", "*.tsx"]

    def test_invalid_field_raises(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".jig" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "formatters.yml").write_text(
            "formatters:\n  - id: x\n    cmd: 'echo'\n    bogus: true\n"
        )
        with pytest.raises(Exception):
            load_formatters(tmp_path)


class TestFormatterModel:
    def test_defaults(self) -> None:
        f = Formatter(id="ruff", cmd="ruff format {files}")
        assert f.files == []
        assert f.autofix is True


class TestLoadEscalationConfig:
    def test_missing_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_escalation_config(tmp_path)
        assert isinstance(cfg, EscalationConfig)
        assert cfg.default_route == "human_review"
        assert cfg.routes == []

    def test_present(self, tmp_path: Path) -> None:
        jig_dir = tmp_path / ".jig"
        jig_dir.mkdir(parents=True)
        (jig_dir / "escalation.yml").write_text(
            "default_route: human_review\n"
            "routes:\n"
            "  - rule_id: no-print\n"
            "    route: agent_resolution\n"
            "  - type: structural_violation\n"
            "    route: human_review\n"
        )
        cfg = load_escalation_config(tmp_path)
        assert cfg.default_route == "human_review"
        assert len(cfg.routes) == 2
        assert cfg.routes[0].rule_id == "no-print"
        assert cfg.routes[0].route == "agent_resolution"


class TestResolveRoute:
    def test_default_when_no_routes(self) -> None:
        cfg = EscalationConfig()
        assert resolve_route(cfg, "anything", "convention_violation") == "human_review"

    def test_rule_id_match_wins(self) -> None:
        cfg = EscalationConfig(
            default_route="human_review",
            routes=[
                EscalationRoute(rule_id="no-print", route="agent_resolution"),
                EscalationRoute(type="convention_violation", route="human_review"),
            ],
        )
        assert resolve_route(cfg, "no-print", "convention_violation") == "agent_resolution"

    def test_type_match_when_rule_unmatched(self) -> None:
        cfg = EscalationConfig(
            default_route="human_review",
            routes=[
                EscalationRoute(type="convention_violation", route="agent_resolution"),
            ],
        )
        assert resolve_route(cfg, "any-rule", "convention_violation") == "agent_resolution"

    def test_default_when_no_match(self) -> None:
        cfg = EscalationConfig(
            default_route="agent_resolution",
            routes=[EscalationRoute(rule_id="x", route="human_review")],
        )
        assert resolve_route(cfg, "y", "convention_violation") == "agent_resolution"

    def test_rule_id_takes_precedence_over_type(self) -> None:
        cfg = EscalationConfig(
            default_route="human_review",
            routes=[
                EscalationRoute(type="convention_violation", route="agent_resolution"),
                EscalationRoute(rule_id="critical-rule", route="human_review"),
            ],
        )
        assert resolve_route(cfg, "critical-rule", "convention_violation") == "human_review"
