"""Tests for canonicalize config loaders."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.canonicalize import (
    Deprecation,
    DeprecationsConfig,
    EscalationConfig,
    EscalationRoute,
    Formatter,
    FormattersConfig,
    list_semgrep_rule_paths,
    load_deprecations,
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


class TestLoadDeprecations:
    def test_missing_returns_empty(self, tmp_path: Path) -> None:
        cfg = load_deprecations(tmp_path)
        assert isinstance(cfg, DeprecationsConfig)
        assert cfg.deprecations == []

    def test_present(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".jig" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "deprecations.yml").write_text(
            "deprecations:\n"
            "  - id: logger-warn-renamed\n"
            "    pattern: logger.warn(...)\n"
            "    fix: logger.warning(...)\n"
            "    rationale: Standardized on .warning()\n"
            "    languages: [python]\n"
        )
        cfg = load_deprecations(tmp_path)
        assert len(cfg.deprecations) == 1
        dep = cfg.deprecations[0]
        assert dep.id == "logger-warn-renamed"
        assert dep.fix == "logger.warning(...)"
        assert dep.languages == ["python"]

    def test_invalid_field_raises(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".jig" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "deprecations.yml").write_text(
            "deprecations:\n  - id: x\n    pattern: p\n    fix: f\n    unknown_field: bad\n"
        )
        with pytest.raises(Exception):
            load_deprecations(tmp_path)

    def test_defaults(self) -> None:
        dep = Deprecation(id="x", pattern="p", fix="f", languages=["generic"])
        assert dep.rationale == ""

    def test_empty_languages_raises(self) -> None:
        with pytest.raises(Exception, match="languages"):
            Deprecation(id="x", pattern="p", fix="f")


class TestDeprecationsToSemgrepRules:
    def test_empty_produces_empty_rules(self) -> None:
        cfg = DeprecationsConfig()
        result = cfg.to_semgrep_rules()
        assert result == {"rules": []}

    def test_rule_structure(self) -> None:
        cfg = DeprecationsConfig(deprecations=[
            Deprecation(id="old-logger", pattern="log.warn(...)", fix="log.warning(...)",
                        rationale="Use .warning()", languages=["python"]),
        ])
        result = cfg.to_semgrep_rules()
        assert len(result["rules"]) == 1
        rule = result["rules"][0]
        assert rule["id"] == "old-logger"
        assert rule["pattern"] == "log.warn(...)"
        assert rule["fix"] == "log.warning(...)"
        assert rule["message"] == "Use .warning()"
        assert rule["languages"] == ["python"]
        assert rule["severity"] == "WARNING"

    def test_fallback_message_when_no_rationale(self) -> None:
        cfg = DeprecationsConfig(deprecations=[
            Deprecation(id="my-rule", pattern="old()", fix="new()", languages=["python"]),
        ])
        rule = cfg.to_semgrep_rules()["rules"][0]
        assert "my-rule" in rule["message"]

    def test_languages_always_emitted(self) -> None:
        cfg = DeprecationsConfig(deprecations=[
            Deprecation(id="x", pattern="p", fix="f", languages=["generic"]),
        ])
        rule = cfg.to_semgrep_rules()["rules"][0]
        assert rule["languages"] == ["generic"]


class TestListSemgrepRulePaths:
    def test_no_rules_dir_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / ".jig").mkdir()
        assert list_semgrep_rule_paths(tmp_path) == []

    def test_semgrep_dir_with_rules(self, tmp_path: Path) -> None:
        semgrep_dir = tmp_path / ".jig" / "rules" / "semgrep"
        semgrep_dir.mkdir(parents=True)
        (semgrep_dir / "rule-a.yml").write_text("rules: []")
        (semgrep_dir / "rule-b.yml").write_text("rules: []")
        paths = list_semgrep_rule_paths(tmp_path)
        names = [p.name for p in paths]
        assert "rule-a.yml" in names
        assert "rule-b.yml" in names

    def test_deprecations_included_when_present(self, tmp_path: Path) -> None:
        rules_dir = tmp_path / ".jig" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "deprecations.yml").write_text("deprecations: []")
        paths = list_semgrep_rule_paths(tmp_path)
        assert any(p.name == "deprecations.yml" for p in paths)

    def test_semgrep_and_deprecations_combined(self, tmp_path: Path) -> None:
        semgrep_dir = tmp_path / ".jig" / "rules" / "semgrep"
        semgrep_dir.mkdir(parents=True)
        (semgrep_dir / "my-rule.yml").write_text("rules: []")
        (tmp_path / ".jig" / "rules" / "deprecations.yml").write_text("deprecations: []")
        paths = list_semgrep_rule_paths(tmp_path)
        names = [p.name for p in paths]
        assert "my-rule.yml" in names
        assert "deprecations.yml" in names

    def test_returns_sorted_semgrep_rules(self, tmp_path: Path) -> None:
        semgrep_dir = tmp_path / ".jig" / "rules" / "semgrep"
        semgrep_dir.mkdir(parents=True)
        for name in ["z-rule.yml", "a-rule.yml", "m-rule.yml"]:
            (semgrep_dir / name).write_text("rules: []")
        paths = list_semgrep_rule_paths(tmp_path)
        semgrep_names = [p.name for p in paths if p.parent.name == "semgrep"]
        assert semgrep_names == sorted(semgrep_names)
