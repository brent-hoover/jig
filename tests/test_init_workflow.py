"""CLI init entry: directory state, stub creation, top-level dispatch."""
from pathlib import Path

from jig.init_workflow import (
    DirState,
    classify_directory,
    create_stub,
)


def test_classify_fresh_parent_missing(tmp_path: Path):
    target = tmp_path / "new-project"
    assert classify_directory(target) == DirState.FRESH


def test_classify_fresh_parent_exists(tmp_path: Path):
    target = tmp_path / "fresh-dir"
    target.mkdir()
    assert classify_directory(target) == DirState.FRESH


def test_classify_already_scaffolded(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    (tmp_path / ".jig" / "project.yaml").write_text(
        "id: x\nname: x\ncreated_at: 2026-01-01T00:00:00Z\n"
        "template_name: python\ntemplate_applied_at: 2026-01-01T00:00:00Z\n"
    )
    assert classify_directory(tmp_path) == DirState.ALREADY_DONE


def test_classify_in_progress_brief_only(tmp_path: Path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    (tmp_path / ".jig" / "project.yaml").write_text(
        "id: x\nname: x\ncreated_at: 2026-01-01T00:00:00Z\n"
    )
    (tmp_path / ".jig" / "spec" / "project.md").write_text("# x\n")
    assert classify_directory(tmp_path) == DirState.IN_PROGRESS


def test_classify_partial_broken(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    # project.yaml missing; this is inconsistent.
    assert classify_directory(tmp_path) == DirState.BROKEN


def test_classify_broken_invalid_yaml(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    (tmp_path / ".jig" / "project.yaml").write_text("not: : valid:\n  - yaml")
    assert classify_directory(tmp_path) == DirState.BROKEN


def test_create_stub(tmp_path: Path):
    target = tmp_path / "new"
    create_stub(target, name="new")
    assert (target / ".jig" / "project.yaml").is_file()
    assert (target / ".jig" / "spec" / "project.md").is_file()
    import yaml
    data = yaml.safe_load((target / ".jig" / "project.yaml").read_text())
    assert data["name"] == "new"
    assert "id" in data
    assert "created_at" in data


def test_create_stub_idempotent_when_consistent(tmp_path: Path):
    target = tmp_path / "new"
    create_stub(target, name="new")
    import yaml
    first = yaml.safe_load((target / ".jig" / "project.yaml").read_text())
    create_stub(target, name="new")  # should not raise, must not overwrite
    second = yaml.safe_load((target / ".jig" / "project.yaml").read_text())
    assert first["id"] == second["id"]
    assert first["created_at"] == second["created_at"]


def test_create_stub_default_brief_content(tmp_path: Path):
    target = tmp_path / "myproj"
    create_stub(target, name="myproj")
    brief = (target / ".jig" / "spec" / "project.md").read_text()
    assert brief.startswith("# myproj")
