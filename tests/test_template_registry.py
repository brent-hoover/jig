from pathlib import Path

import pytest

from jig.template_registry import (
    TemplateMetadata,
    list_templates,
    load_template_metadata,
)


def test_list_templates_returns_shipped_set():
    names = list_templates()
    assert "python" in names
    assert "fastapi" in names


def test_python_template_metadata():
    md = load_template_metadata("python")
    assert isinstance(md, TemplateMetadata)
    assert md.name == "python"
    assert md.language == "python"


def test_fastapi_template_metadata():
    md = load_template_metadata("fastapi")
    assert md.language == "python"
    assert md.framework == "fastapi"


def test_load_unknown_template_raises():
    with pytest.raises(KeyError):
        load_template_metadata("does-not-exist")


def test_template_without_metadata_file_raises(tmp_path: Path, monkeypatch):
    fake_root = tmp_path / "templates"
    fake_root.mkdir()
    (fake_root / "bare").mkdir()
    monkeypatch.setattr(
        "jig.template_registry._templates_root", lambda: fake_root
    )
    with pytest.raises(ValueError, match="missing template.yaml"):
        load_template_metadata("bare")


def test_template_name_derived_from_directory(tmp_path: Path, monkeypatch):
    """Even if template.yaml declares a different name, the directory
    name is authoritative. This prevents silent name drift.
    """
    fake_root = tmp_path / "templates"
    fake_root.mkdir()
    tpl = fake_root / "realname"
    tpl.mkdir()
    (tpl / "template.yaml").write_text(
        "name: lying-name\nlanguage: python\n"
    )
    monkeypatch.setattr(
        "jig.template_registry._templates_root", lambda: fake_root
    )
    md = load_template_metadata("realname")
    assert md.name == "realname"
