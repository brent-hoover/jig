"""Hidden behavioral tests for the ``todo_cli`` task.

These run inside the sandbox against the candidate's ``todo.py``. Each test
gets its own ``tmp_path`` so the CLI's persistence file is isolated per test.

The tests are intentionally *lenient about format* — they verify behaviors
described by the spec ("items appear in order", "completed items are
visually distinguishable from open ones"), not specific output strings.
A model that lists items as ``1) buy milk`` or ``- buy milk`` or
``1. buy milk`` should all pass; what's checked is the behavior.
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


def _nonblank_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _line_for(item_text: str, lines: list[str]) -> str:
    """Find the unique line that mentions ``item_text``; assert exactly one."""
    matches = [line for line in lines if item_text in line]
    assert len(matches) == 1, (
        f"expected exactly one line mentioning {item_text!r}, "
        f"found {len(matches)}: {matches!r}"
    )
    return matches[0]


def test_list_is_empty_before_anything_is_added(tmp_path: Path) -> None:
    result = run("list", cwd=tmp_path)
    assert result.returncode == 0
    assert _nonblank_lines(result.stdout) == []


def test_added_item_appears_in_list(tmp_path: Path) -> None:
    add = run("add", "buy milk", cwd=tmp_path)
    assert add.returncode == 0

    listed = run("list", cwd=tmp_path)
    assert listed.returncode == 0
    assert "buy milk" in listed.stdout


def test_multiple_items_appear_in_insertion_order(tmp_path: Path) -> None:
    for item in ("buy milk", "feed cat", "read book"):
        assert run("add", item, cwd=tmp_path).returncode == 0

    output = run("list", cwd=tmp_path).stdout
    milk_at = output.find("buy milk")
    cat_at = output.find("feed cat")
    book_at = output.find("read book")
    assert -1 < milk_at < cat_at < book_at, f"items out of order in:\n{output!r}"


def test_each_item_appears_on_its_own_line(tmp_path: Path) -> None:
    for item in ("alpha", "beta", "gamma"):
        run("add", item, cwd=tmp_path)
    lines = _nonblank_lines(run("list", cwd=tmp_path).stdout)
    assert len(lines) == 3


def test_completed_item_is_visually_distinguishable(tmp_path: Path) -> None:
    """The spec says open vs completed should be visually distinguishable.
    We don't care HOW (``[x]``, ``✓``, ``(done)``, strikethrough, etc.) —
    only that the line for a completed item differs from the line for an
    otherwise-identical open item."""
    run("add", "buy milk", cwd=tmp_path)
    run("add", "feed cat", cwd=tmp_path)

    before_lines = _nonblank_lines(run("list", cwd=tmp_path).stdout)
    before_milk = _line_for("buy milk", before_lines)
    before_cat = _line_for("feed cat", before_lines)

    done = run("done", "1", cwd=tmp_path)
    assert done.returncode == 0

    after_lines = _nonblank_lines(run("list", cwd=tmp_path).stdout)
    after_milk = _line_for("buy milk", after_lines)
    after_cat = _line_for("feed cat", after_lines)

    assert after_milk != before_milk, (
        "expected line for marked-done item to change; got identical line "
        f"{after_milk!r}"
    )
    assert after_cat == before_cat, (
        "expected line for untouched item to remain identical; got "
        f"{before_cat!r} → {after_cat!r}"
    )


def test_done_with_invalid_position_reports_an_error(tmp_path: Path) -> None:
    """Spec says invalid `done` is reported clearly rather than silent. We
    accept any error signal: a non-zero exit code OR output on stderr."""
    run("add", "buy milk", cwd=tmp_path)
    result = run("done", "99", cwd=tmp_path)
    assert result.returncode != 0 or result.stderr.strip(), (
        "expected an error signal for done on a missing position; got "
        f"returncode={result.returncode}, stderr={result.stderr!r}"
    )


def test_state_persists_across_invocations(tmp_path: Path) -> None:
    """Each ``run()`` is a fresh process — anything in-memory is gone.
    Items must survive on disk."""
    run("add", "buy milk", cwd=tmp_path)
    listed = run("list", cwd=tmp_path)
    assert listed.returncode == 0
    assert "buy milk" in listed.stdout
