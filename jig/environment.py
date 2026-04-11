from pathlib import Path


def load_environment_md(project_path: Path) -> str:
    """Load .jig/environment.md verbatim, or return '' if absent."""
    path = project_path / ".jig" / "environment.md"
    if not path.is_file():
        return ""
    return path.read_text()
