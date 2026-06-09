"""Git-style project-root discovery for the issue front doors."""

from pathlib import Path


def find_project_root(start: Path) -> Path:
    """Return the nearest ancestor of ``start`` (inclusive) containing a
    ``.jig/`` directory.

    Lets the ``jig issue`` CLI and the standalone MCP run from any
    subdirectory of a project, the way git locates ``.git/``. Raises
    ``FileNotFoundError`` with a clear message when ``start`` is not inside any
    jig project.
    """
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".jig").is_dir():
            return candidate
    raise FileNotFoundError(
        f"{start} is not inside a jig project (no .jig/ directory found in any "
        "parent). Run from within a project, or pass --path."
    )
