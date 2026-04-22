from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any

import yaml

from jig.project import Project


@dataclass(frozen=True)
class Skill:
    name: str
    source_filename: str
    applies_to: dict[str, Any]
    content: str


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body. Returns (frontmatter_dict, body)."""
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---\n", 4)
    if end == -1:
        return {}, raw
    front = raw[4:end]
    body = raw[end + len("\n---\n") :].lstrip("\n")
    data = yaml.safe_load(front) or {}
    return data, body


def load_all_skills() -> list[Skill]:
    """Load every skill shipped under jig/skills/, filename-sorted."""
    pkg = resources.files("jig.skills")
    results: list[Skill] = []
    for entry in sorted(pkg.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".md"):
            continue
        raw = entry.read_text(encoding="utf-8")
        front, body = _parse_frontmatter(raw)
        name = front.get("name", entry.name.removesuffix(".md"))
        applies_to = front.get("applies_to") or {}
        results.append(
            Skill(
                name=name,
                source_filename=entry.name,
                applies_to=applies_to,
                content=body,
            )
        )
    return results


def _matches(applies_to: dict[str, Any], project: Project) -> bool:
    if not applies_to:
        return True
    for key, expected in applies_to.items():
        actual = getattr(project, key, None)
        if actual != expected:
            return False
    return True


def match_skills(*, project: Project, skills: list[Skill]) -> list[Skill]:
    """Return skills whose applies_to block matches the project. Filename-sorted."""
    return [skill for skill in skills if _matches(skill.applies_to, project)]
