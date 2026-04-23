"""Tests for ``jig.logging_setup`` — centralised logging config."""

from __future__ import annotations

import logging
from pathlib import Path

from jig.logging_setup import configure_logging


def test_configure_logging_creates_log_file_and_handlers(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    log_file = configure_logging(tmp_path, verbose=False)

    assert log_file.exists()
    assert log_file.parent == tmp_path / ".jig" / "logs"
    assert log_file.name.startswith("jig-")

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    # Console + file handler
    handler_types = [type(h).__name__ for h in root.handlers]
    assert "StreamHandler" in handler_types
    assert "FileHandler" in handler_types


def test_configure_logging_quiets_noisy_loggers(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    configure_logging(tmp_path, verbose=False)

    assert logging.getLogger("websockets").level == logging.WARNING
    assert logging.getLogger("mcp").level == logging.WARNING
