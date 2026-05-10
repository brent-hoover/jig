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


def load_escalation_config(project_path: Path) -> EscalationConfig:
    """Load .jig/escalation.yml; return defaults when missing."""
    path = project_path / ".jig" / "escalation.yml"
    if not path.is_file():
        return EscalationConfig()
    data = yaml.safe_load(path.read_text()) or {}
    return EscalationConfig.model_validate(data)


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
    "EscalationConfig",
    "EscalationRoute",
    "Formatter",
    "FormattersConfig",
    "Route",
    "SemgrepRule",
    "load_escalation_config",
    "load_formatters",
    "resolve_route",
]
