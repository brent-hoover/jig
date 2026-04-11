from jig.project import Project
from jig.skill_loader import load_all_skills, match_skills


def _project(**overrides) -> Project:
    """Minimal Project factory."""
    return Project(
        id="x",
        name="x",
        path="/tmp",
        language=overrides.get("language", ""),
        package_manager=overrides.get("package_manager", ""),
    )


def test_load_all_skills_returns_every_skill() -> None:
    skills = load_all_skills()
    names = {s.name for s in skills}
    assert "jig-mcp-tools" in names
    assert "git-conventions" in names
    assert "python" in names
    assert "uv" in names
    assert "typescript" in names


def test_universal_skills_always_match() -> None:
    skills = load_all_skills()
    matched = match_skills(project=_project(), skills=skills)
    names = {s.name for s in matched}
    assert "jig-mcp-tools" in names
    assert "git-conventions" in names


def test_python_uv_project_matches_uv_skill() -> None:
    skills = load_all_skills()
    project = _project(language="python", package_manager="uv")
    matched = match_skills(project=project, skills=skills)
    names = {s.name for s in matched}
    assert "python" in names
    assert "uv" in names
    assert "typescript" not in names
    assert "pnpm" not in names


def test_typescript_pnpm_project_matches_ts_skills() -> None:
    skills = load_all_skills()
    project = _project(language="typescript", package_manager="pnpm")
    matched = match_skills(project=project, skills=skills)
    names = {s.name for s in matched}
    assert "typescript" in names
    assert "pnpm" in names
    assert "vitest" in names
    assert "python" not in names
    assert "uv" not in names


def test_match_is_filename_sorted() -> None:
    skills = load_all_skills()
    project = _project(language="python", package_manager="uv")
    matched = match_skills(project=project, skills=skills)
    sources = [s.source_filename for s in matched]
    assert sources == sorted(sources)


def test_skill_content_contains_frontmatter_stripped() -> None:
    skills = load_all_skills()
    python_skill = next(s for s in skills if s.name == "python")
    assert "---" not in python_skill.content.split("\n")[0]
    assert "# Python conventions" in python_skill.content
