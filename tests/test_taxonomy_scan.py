"""Real-ruff tests for the taxonomy scan — fires on B006/T201/BLE001 and maps
each ruff finding to its taxonomy id."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.code_quality.taxonomy import scan_taxonomy


def test_scan_maps_ruff_findings_to_taxonomy_ids(tmp_path: Path) -> None:
    f = tmp_path / "bad.py"
    f.write_text(
        "def g(x=[]):\n"  # B006  -> TAX-LANG-001
        "    print('hi')\n"  # T201  -> TAX-OBS-001
        "    try:\n"
        "        pass\n"
        "    except Exception:\n"  # BLE001 -> TAX-ERR-001
        "        pass\n"
    )
    hits = scan_taxonomy(tmp_path, [f])
    ids = {h.id for h in hits}
    assert "TAX-LANG-001" in ids
    assert "TAX-OBS-001" in ids
    assert "TAX-ERR-001" in ids
    for h in hits:
        assert h.file.endswith("bad.py")
        assert h.line > 0
        assert h.reviewer  # owning reviewer carried through


def test_scan_clean_file_no_hits(tmp_path: Path) -> None:
    f = tmp_path / "ok.py"
    f.write_text("def g(x: int) -> int:\n    return x + 1\n")
    assert scan_taxonomy(tmp_path, [f]) == []


def test_scan_hit_paths_are_repo_relative(tmp_path: Path) -> None:
    """``TaxonomyHit.file`` must be repo-relative — the reviewer sees the
    worktree mounted at ``/workspace`` in sandbox, so a host-absolute path
    would leak host state AND be unusable for the reviewer."""
    (tmp_path / "pkg").mkdir()
    f = tmp_path / "pkg" / "bad.py"
    f.write_text("def g(x=[]):\n    return x\n")  # B006 -> TAX-LANG-001
    hits = scan_taxonomy(tmp_path, [f])
    assert hits
    for h in hits:
        assert not Path(h.file).is_absolute(), f"absolute path leaked: {h.file}"
        # And must be reconstructable to the original under the worktree.
        assert (tmp_path / h.file).resolve() == f.resolve()


def test_scan_degrades_on_malformed_ruff_output(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch"
) -> None:
    """Signal-only contract: if ruff emits valid JSON with an unexpected shape
    (a non-dict finding, a non-dict location, a non-numeric row), the scan must
    degrade to ``[]`` — not raise into the async caller."""
    import subprocess

    from jig.code_quality import taxonomy as taxonomy_mod

    # A list-of-strings is valid JSON but a string has no ``.get`` — would raise
    # AttributeError if the parsing loop weren't guarded.
    fake = subprocess.CompletedProcess(
        args=[], returncode=1, stdout='["weird"]', stderr=""
    )
    monkeypatch.setattr(taxonomy_mod.subprocess, "run", lambda *_a, **_k: fake)

    f = tmp_path / "bad.py"
    f.write_text("def g(x=[]):\n    return x\n")
    assert scan_taxonomy(tmp_path, [f]) == []


def test_scan_ignores_target_project_ruff_config(tmp_path: Path) -> None:
    """The taxonomy signal must be jig-owned and comparable — a target repo's
    ruff config (e.g. ``ignore = ["B006"]`` or per-file-ignores) must not be
    able to suppress taxonomy hits. ``ruff --isolated`` enforces this."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff.lint]\n"
        'ignore = ["B006", "T201", "BLE001"]\n'
        "[tool.ruff.lint.per-file-ignores]\n"
        '"bad.py" = ["B006", "T201", "BLE001"]\n'
    )
    f = tmp_path / "bad.py"
    f.write_text("def g(x=[]):\n    return x\n")  # B006 -> TAX-LANG-001
    hits = scan_taxonomy(tmp_path, [f])
    assert any(h.id == "TAX-LANG-001" for h in hits), (
        f"target ruff config suppressed the taxonomy hit: {hits}"
    )
