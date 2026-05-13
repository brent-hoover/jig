"""Hidden tests for the ``todo_cli`` task.

These run inside the sandbox against the candidate's ``todo.py``. Each test
gets its own ``tmp_path`` so the CLI's persistence file is isolated per test.
"""

import subprocess
import sys
from pathlib import Path

ENTRY = Path(__file__).resolve().parent.parent / "todo.py"


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=5,
    )


def test_list_empty_prints_nothing(tmp_path: Path) -> None:
    result = run("list", cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout == ""


def test_add_then_list_shows_item(tmp_path: Path) -> None:
    add = run("add", "buy milk", cwd=tmp_path)
    assert add.returncode == 0

    listed = run("list", cwd=tmp_path)
    assert listed.returncode == 0
    assert listed.stdout.strip() == "1. buy milk"


def test_add_multiple_preserves_insertion_order(tmp_path: Path) -> None:
    for item in ("buy milk", "feed cat", "read book"):
        assert run("add", item, cwd=tmp_path).returncode == 0

    listed = run("list", cwd=tmp_path).stdout.strip().splitlines()
    assert listed == ["1. buy milk", "2. feed cat", "3. read book"]


def test_done_marks_item_with_x_prefix(tmp_path: Path) -> None:
    run("add", "buy milk", cwd=tmp_path)
    run("add", "feed cat", cwd=tmp_path)

    done = run("done", "1", cwd=tmp_path)
    assert done.returncode == 0

    listed = run("list", cwd=tmp_path).stdout.strip().splitlines()
    assert listed == ["1. [x] buy milk", "2. feed cat"]


def test_done_with_out_of_range_index_fails(tmp_path: Path) -> None:
    run("add", "buy milk", cwd=tmp_path)
    result = run("done", "99", cwd=tmp_path)
    assert result.returncode != 0


def test_state_persists_across_invocations(tmp_path: Path) -> None:
    run("add", "buy milk", cwd=tmp_path)
    # Fresh subprocess — anything in-memory is gone; only disk state remains.
    listed = run("list", cwd=tmp_path)
    assert listed.returncode == 0
    assert listed.stdout.strip() == "1. buy milk"
