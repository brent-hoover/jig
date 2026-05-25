"""Tests for ``load_role`` resolving role IDs whose filename differs.

Many shipped roles use hyphenated IDs (``reviewer-test-adequacy``,
``planner-pm``, ``po-l0``) but underscored / reordered filenames
(``reviewer_test_adequacy.yaml``, ``planner_pm.yaml``, ``l0_po.yaml``).
The orchestrator's federation spawn path threads an explicit
``role_file`` arg through ``spawn_review_agent_for_id``, so it works
fine. The TUI's AgentsScreen calls ``load_role(path, agent.role)``
with the hyphenated id (per ``_load_role_config`` on each
``handle_agent_start``); a regression there previously raised
FileNotFoundError and left the underlying ``AgentState`` with empty
``allowed_tools`` / ``phase_prompt`` fields.

This test pins the lookup contract: ``load_role`` must resolve every
shipped role by its ``role:`` field value, regardless of how the file
is named.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.persistence import load_role


SHIPPED_ROLES_WITH_NONMATCHING_FILES = [
    "reviewer-test-adequacy",
    "reviewer-pattern-conformance",
    "reviewer-error-handling",
    "reviewer-architectural",
    "reviewer-performance",
    "reviewer-security",
    "planner-pm",
    "sa-mvp",
    "sa-v2",
    "po-l0",
    "po-l1",
    "po-l2",
    "po-l3",
]


@pytest.mark.parametrize("role_id", SHIPPED_ROLES_WITH_NONMATCHING_FILES)
def test_resolves_role_by_id_when_filename_differs(
    tmp_path: Path, role_id: str
) -> None:
    """Every shipped role whose filename doesn't match its ``role:`` field
    must still be findable via ``load_role(path, role_id)``."""
    cfg = load_role(tmp_path, role_id)
    assert cfg.role == role_id


def test_direct_filename_match_still_works(tmp_path: Path) -> None:
    """The common case — role id matches filename basename — must still
    work via the fast project-override / shipped-default lookup, not get
    routed through the slow list-roles scan."""
    cfg = load_role(tmp_path, "dev")
    assert cfg.role == "dev"


def test_unknown_role_still_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_role(tmp_path, "this-role-does-not-exist")
