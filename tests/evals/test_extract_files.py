"""Tests for multi-file extraction (extract_files).

Verifies that named code blocks land at the right filenames under each of
the markdown patterns models commonly produce, and that fallback to the
default filename happens correctly for single unlabeled blocks.
"""

import pytest

from jig.evals.prompt_style_eval.classify import extract_files


def test_extract_files_empty_when_no_blocks() -> None:
    assert extract_files("just prose, no code", default_filename="app.py") == {}


def test_extract_single_unlabeled_block_uses_default() -> None:
    text = "Here:\n```python\nprint('hi')\n```"
    out = extract_files(text, default_filename="app.py")
    assert out == {"app.py": "print('hi')"}


def test_bold_heading_names_the_file() -> None:
    text = (
        "**app.py**\n\n"
        "```python\nfrom fastapi import FastAPI\napp = FastAPI()\n```\n\n"
        "**client.py**\n\n"
        "```python\nimport httpx\nclass C: pass\n```"
    )
    out = extract_files(text, default_filename="app.py")
    assert set(out) == {"app.py", "client.py"}
    assert "FastAPI" in out["app.py"]
    assert "httpx" in out["client.py"]


def test_markdown_heading_names_the_file() -> None:
    text = (
        "### app.py\n\n```python\nx = 1\n```\n\n### client.py\n\n```python\ny = 2\n```"
    )
    out = extract_files(text, default_filename="app.py")
    assert out == {"app.py": "x = 1", "client.py": "y = 2"}


def test_first_line_comment_names_the_file() -> None:
    text = (
        "```python\n# app.py\nimport sys\n```\n\n"
        "```python\n# client.py\nimport httpx\n```"
    )
    out = extract_files(text, default_filename="app.py")
    assert "# app.py" in out["app.py"]
    assert "# client.py" in out["client.py"]


def test_heading_takes_precedence_over_first_line_comment() -> None:
    text = "**server.py**\n\n```python\n# something_else.py\nx = 1\n```"
    out = extract_files(text, default_filename="app.py")
    assert out == {"server.py": "# something_else.py\nx = 1"}


def test_file_colon_label_supported() -> None:
    text = "File: app.py\n\n```python\nx = 1\n```"
    out = extract_files(text, default_filename="default.py")
    assert out == {"app.py": "x = 1"}


def test_filename_colon_label_supported() -> None:
    text = "app.py:\n\n```python\nx = 1\n```"
    out = extract_files(text, default_filename="default.py")
    assert out == {"app.py": "x = 1"}


def test_subsequent_unlabeled_block_is_dropped() -> None:
    text = (
        "```python\nfirst = 1\n```\n\nAnd here's another:\n\n```python\nsecond = 2\n```"
    )
    out = extract_files(text, default_filename="app.py")
    # First gets the default; second is unlabeled and dropped.
    assert out == {"app.py": "first = 1"}


@pytest.mark.parametrize(
    "heading",
    [
        "**app.py**",
        "### app.py",
        "## app.py",
        "# app.py",
        "File: app.py",
        "Filename: app.py",
        "app.py:",
    ],
)
def test_various_heading_forms(heading: str) -> None:
    text = f"{heading}\n\n```python\nx = 1\n```"
    out = extract_files(text, default_filename="default.py")
    assert "app.py" in out
