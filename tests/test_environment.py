from pathlib import Path

from jig.environment import load_environment_md


def test_returns_empty_when_missing(tmp_path: Path) -> None:
    assert load_environment_md(tmp_path) == ""


def test_returns_verbatim_when_present(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    content = "## Quirks\n\n- Never run bare pytest\n"
    (tmp_path / ".jig" / "environment.md").write_text(content)
    assert load_environment_md(tmp_path) == content
