import json
from pathlib import Path

from pydantic import BaseModel


class Project(BaseModel):
    id: str
    name: str
    path: str
    default_branch: str = "main"
    description: str = ""
    language: str = ""
    framework: str = ""
    package_manager: str = ""
    test_command: str = ""
    build_command: str = ""

    def path_or_default(self) -> Path:
        return Path(self.path)


def _project_file(project_path: Path) -> Path:
    return project_path / ".jig" / "project.json"


def save_project(project_path: Path, project: Project) -> None:
    path = _project_file(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(project.model_dump(), indent=2))


def load_project(project_path: Path) -> Project:
    path = _project_file(project_path)
    if not path.is_file():
        raise FileNotFoundError(f"Project file not found: {path}")
    return Project.model_validate_json(path.read_text())
