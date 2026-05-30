"""Real-ruff tests for the taxonomy scan — fires on B006/T201/BLE001 and maps
each ruff finding to its taxonomy id."""

from __future__ import annotations

from pathlib import Path

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
