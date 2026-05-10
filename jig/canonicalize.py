"""Canonicalizer config models and loaders."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


Route = Literal["human_review", "agent_resolution"]


class Formatter(BaseModel):
    """One formatter command + its file globs."""

    model_config = ConfigDict(extra="forbid")

    id: str
    cmd: str
    files: list[str] = Field(default_factory=list)
    autofix: bool = True


class FormattersConfig(BaseModel):
    """Top-level formatters config — list of formatters."""

    model_config = ConfigDict(extra="forbid")

    formatters: list[Formatter] = Field(default_factory=list)


class Deprecation(BaseModel):
    """One convention-drift rule: a superseded pattern and its replacement.

    Loaded from ``.jig/rules/deprecations.yml`` and passed to semgrep
    as an inline rule set for autofix sweeps.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    pattern: str
    fix: str
    languages: list[str]
    rationale: str = ""


class DeprecationsConfig(BaseModel):
    """Top-level deprecations config — list of deprecation rules."""

    model_config = ConfigDict(extra="forbid")

    deprecations: list[Deprecation] = Field(default_factory=list)

    def to_semgrep_rules(self) -> dict:
        """Render as a semgrep rule manifest (``rules`` list).

        The returned dict can be serialised to YAML and passed to
        ``semgrep --config <file>`` for in-process or subprocess use.
        """
        rules = []
        for dep in self.deprecations:
            rule: dict = {
                "id": dep.id,
                "pattern": dep.pattern,
                "fix": dep.fix,
                "message": dep.rationale or f"Deprecated: {dep.id}",
                "severity": "WARNING",
            }
            rule["languages"] = dep.languages
            rules.append(rule)
        return {"rules": rules}


class SemgrepRule(BaseModel):
    """A semgrep rule source — directory or single rule glob."""

    model_config = ConfigDict(extra="forbid")

    path: str


class EscalationRoute(BaseModel):
    """Per-rule or per-issue-type routing override."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str | None = None
    type: str | None = None
    route: Route


class EscalationConfig(BaseModel):
    """Escalation routing config: default + per-rule/type overrides."""

    model_config = ConfigDict(extra="forbid")

    default_route: Route = "human_review"
    routes: list[EscalationRoute] = Field(default_factory=list)


def load_formatters(project_path: Path) -> FormattersConfig:
    """Load .jig/rules/formatters.yml; return empty config when missing."""
    path = project_path / ".jig" / "rules" / "formatters.yml"
    if not path.is_file():
        return FormattersConfig()
    data = yaml.safe_load(path.read_text()) or {}
    return FormattersConfig.model_validate(data)


def load_deprecations(project_path: Path) -> DeprecationsConfig:
    """Load .jig/rules/deprecations.yml; return empty config when missing."""
    path = project_path / ".jig" / "rules" / "deprecations.yml"
    if not path.is_file():
        return DeprecationsConfig()
    data = yaml.safe_load(path.read_text()) or {}
    return DeprecationsConfig.model_validate(data)


def list_semgrep_rule_paths(project_path: Path) -> list[Path]:
    """Return all active semgrep rule paths for this project.

    Includes every ``.yml`` file under ``.jig/rules/semgrep/`` (project
    rules) plus ``.jig/rules/deprecations.yml`` when present.  Returns an
    empty list when neither exists.
    """
    paths: list[Path] = []
    semgrep_dir = project_path / ".jig" / "rules" / "semgrep"
    if semgrep_dir.is_dir():
        paths.extend(sorted(p for p in semgrep_dir.glob("*.yml") if p.is_file()))
    deprecations = project_path / ".jig" / "rules" / "deprecations.yml"
    if deprecations.is_file():
        paths.append(deprecations)
    return paths


def load_escalation_config(project_path: Path) -> EscalationConfig:
    """Load .jig/escalation.yml; return defaults when missing."""
    path = project_path / ".jig" / "escalation.yml"
    if not path.is_file():
        return EscalationConfig()
    data = yaml.safe_load(path.read_text()) or {}
    return EscalationConfig.model_validate(data)


def check_conventions(project_path: Path) -> list[str]:
    """Validate .jig/conventions.md; return a list of error strings (empty = OK)."""
    path = project_path / ".jig" / "conventions.md"
    if not path.is_file():
        return [".jig/conventions.md is missing"]
    content = path.read_text()
    if not content.strip():
        return [".jig/conventions.md is empty"]
    lines = content.splitlines()
    if len(lines) > 500:
        return [f".jig/conventions.md is {len(lines)} lines — recommended maximum is 500 (long contexts degrade agent quality)"]
    return []


def _rule_ids_from_semgrep_file(path: Path) -> list[str]:
    """Parse rule IDs from a semgrep-format .yml file."""
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return []
    rules = data.get("rules", [])
    return [r["id"] for r in rules if isinstance(r, dict) and "id" in r]


def check_rule_coverage(project_path: Path) -> dict[str, list[str]]:
    """Cross-reference rules in .jig/rules/ against .jig/conventions.md.

    Returns a dict with two keys:
    - ``undocumented``: rule IDs present in rule files but not mentioned in
      conventions.md (agents won't see guidance for these at task start).
    - ``missing_conventions``: empty list when conventions.md is absent
      (coverage check is skipped).
    """
    conventions_path = project_path / ".jig" / "conventions.md"
    if not conventions_path.is_file():
        return {"undocumented": [], "missing_conventions": [".jig/conventions.md not found — skipping coverage check"]}

    conventions_text = conventions_path.read_text()

    rule_ids: list[str] = []
    semgrep_dir = project_path / ".jig" / "rules" / "semgrep"
    if semgrep_dir.is_dir():
        for rule_file in sorted(p for p in semgrep_dir.glob("*.yml") if p.is_file()):
            rule_ids.extend(_rule_ids_from_semgrep_file(rule_file))

    deprecations_path = project_path / ".jig" / "rules" / "deprecations.yml"
    if deprecations_path.is_file():
        dep_cfg = load_deprecations(project_path)
        rule_ids.extend(dep.id for dep in dep_cfg.deprecations)

    undocumented = [rid for rid in rule_ids if rid not in conventions_text]
    return {"undocumented": undocumented, "missing_conventions": []}


def resolve_route(
    config: EscalationConfig, rule_id: str, issue_type: str
) -> Route:
    """Pick the route for a (rule_id, issue_type) pair.

    Rule-id matches take precedence over type matches; otherwise the
    config's ``default_route`` applies.
    """
    for r in config.routes:
        if r.rule_id is not None and r.rule_id == rule_id:
            return r.route
    for r in config.routes:
        if r.type is not None and r.type == issue_type:
            return r.route
    return config.default_route


__all__ = [
    "Deprecation",
    "DeprecationsConfig",
    "EscalationConfig",
    "EscalationRoute",
    "Formatter",
    "FormattersConfig",
    "Route",
    "SemgrepRule",
    "check_conventions",
    "check_rule_coverage",
    "list_semgrep_rule_paths",
    "load_deprecations",
    "load_escalation_config",
    "load_formatters",
    "resolve_route",
]
