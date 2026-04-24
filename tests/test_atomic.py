from pathlib import Path

import pytest

from jig.atomic import atomic_write_text


def test_atomic_write_creates_file(tmp_path: Path):
    target = tmp_path / "data.txt"
    atomic_write_text(target, "hello\n")
    assert target.read_text() == "hello\n"


def test_atomic_write_replaces_existing(tmp_path: Path):
    target = tmp_path / "data.txt"
    target.write_text("old")
    atomic_write_text(target, "new")
    assert target.read_text() == "new"


def test_atomic_write_creates_parent(tmp_path: Path):
    target = tmp_path / "nested" / "dir" / "data.txt"
    atomic_write_text(target, "x")
    assert target.read_text() == "x"


def test_atomic_write_no_partial_on_interrupt(tmp_path: Path, monkeypatch):
    """If os.replace raises, the original file must be untouched."""
    import os

    target = tmp_path / "data.txt"
    target.write_text("original")

    orig_replace = os.replace

    def bad_replace(src, dst):
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", bad_replace)
    with pytest.raises(OSError):
        atomic_write_text(target, "corrupted")
    monkeypatch.setattr(os, "replace", orig_replace)
    assert target.read_text() == "original"
