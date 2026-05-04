import logging
from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--run-real`` so real-mode wiring tests opt in.

    Real-mode tests exercise the orchestrator-spawn path with a
    monkeypatched Claude SDK (no LLM cost). They're skipped by default
    so the bones CI run stays fast; pass ``--run-real`` (or use ``-m
    real``) to include them.
    """
    parser.addoption(
        "--run-real",
        action="store_true",
        default=False,
        help="run tests marked with @pytest.mark.real",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip ``@pytest.mark.real`` tests unless the operator opts in.

    Opt-in via ``--run-real`` flag or ``-m real`` selector. Skipping at
    collection time keeps the default suite fast (real-mode wiring tests
    spin up an Orchestrator + monkeypatched SDK) without forcing every
    test author to add an opt-in fixture.
    """
    if config.getoption("--run-real"):
        return
    # Honor ``-m real`` so operators can run only the real-mode tests.
    marker_expr = config.getoption("-m") or ""
    if "real" in marker_expr:
        return
    skip_real = pytest.mark.skip(
        reason="real-mode wiring test; opt in with --run-real or -m real"
    )
    for item in items:
        if "real" in item.keywords:
            item.add_marker(skip_real)


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
