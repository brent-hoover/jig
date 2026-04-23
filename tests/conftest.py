import logging
from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary directory simulating a git repo."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Snapshot + restore root logger handlers and level.

    ``jig.logging_setup.configure_logging`` mutates the root logger;
    without this fixture, handlers stack across tests and the log
    file handler holds the log file open (causing Windows test
    issues too). Autouse at the conftest level so any test that
    calls ``configure_logging`` gets clean teardown.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        for h in root.handlers:
            if h not in saved_handlers:
                h.close()
        root.handlers = saved_handlers
        root.level = saved_level
