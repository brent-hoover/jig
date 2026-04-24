from pathlib import Path

import pytest

from jig.markdown_sections import (
    get_section,
    list_sections,
    set_section,
)


SAMPLE = """# todoapp

Task management for individuals.

## Built

- **CRUD for todos** — done

## Planned (committed)

### Due dates
Users should be able to give todos due dates.

## Non-goals

- Sharing
"""


def test_list_sections_returns_level_two_headings(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    assert list_sections(p) == ["Built", "Planned (committed)", "Non-goals"]


def test_get_section_returns_body_without_heading(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    built = get_section(p, "Built")
    assert built.strip() == "- **CRUD for todos** — done"


def test_get_section_missing_raises(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    with pytest.raises(KeyError):
        get_section(p, "Backlog")


def test_set_section_replaces_existing(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    set_section(p, "Non-goals", "- Sharing\n- Calendar\n")
    text = p.read_text()
    assert "- Calendar" in text
    assert text.count("## Non-goals") == 1


def test_set_section_appends_when_missing(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text(SAMPLE)
    set_section(p, "Backlog", "- Mobile app\n")
    text = p.read_text()
    assert "## Backlog" in text
    assert "- Mobile app" in text


def test_set_section_on_empty_file_creates_structure(tmp_path: Path):
    p = tmp_path / "brief.md"
    p.write_text("# myproj\n")
    set_section(p, "Built", "- first capability\n")
    text = p.read_text()
    assert "# myproj" in text
    assert "## Built" in text
    assert "- first capability" in text
