"""Regression tests: every id-derived path helper rejects ``..``-style escapes.

Block 1 deliverable 4 — apply ``safe_path`` validation at every place
an id-shaped string is concatenated into a path. One test per source
file ensures the boundary check is wired and stays wired.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# spec_loader — every id-derived helper
# ---------------------------------------------------------------------------


def test_suite_dir_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import suite_dir

    with pytest.raises(ValueError):
        suite_dir(tmp_path, "../etc")


def test_module_dir_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import module_dir

    with pytest.raises(ValueError):
        module_dir(tmp_path, "../etc/passwd")


def test_module_contracts_path_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import module_contracts_path

    with pytest.raises(ValueError):
        module_contracts_path(tmp_path, "../escape")


def test_suite_brief_path_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import suite_brief_path

    with pytest.raises(ValueError):
        suite_brief_path(tmp_path, "../escape")


def test_suite_structured_path_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import suite_structured_path

    with pytest.raises(ValueError):
        suite_structured_path(tmp_path, "../escape")


def test_wireframe_path_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import wireframe_path

    with pytest.raises(ValueError):
        wireframe_path(tmp_path, "../escape")


def test_wireframe_notes_path_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import wireframe_notes_path

    with pytest.raises(ValueError):
        wireframe_notes_path(tmp_path, "../escape")


def test_cascade_proposal_path_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import cascade_proposal_path

    with pytest.raises(ValueError):
        cascade_proposal_path(tmp_path, "../escape", "20260101T000000")


def test_discovery_playback_path_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import discovery_playback_path

    with pytest.raises(ValueError):
        discovery_playback_path(tmp_path, "../escape")


def test_generated_contract_path_rejects_traversal(tmp_path: Path) -> None:
    from jig.spec_loader import generated_contract_path

    with pytest.raises(ValueError):
        generated_contract_path(tmp_path, "../etc", "ok")
    with pytest.raises(ValueError):
        generated_contract_path(tmp_path, "ok", "../etc")


# ---------------------------------------------------------------------------
# dev_env/fixtures — service_id
# ---------------------------------------------------------------------------


def test_fixture_store_path_rejects_traversal(tmp_path: Path) -> None:
    from jig.dev_env.fixtures import fixture_store_path

    with pytest.raises(ValueError):
        fixture_store_path(tmp_path, "../escape")


# ---------------------------------------------------------------------------
# dev_env/ephemeral — service.id propagated through provisioner
# ---------------------------------------------------------------------------


def test_sqlite_ephemeral_db_path_rejects_unsafe_service_id(tmp_path: Path) -> None:
    """A ManifestService can't be constructed with an unsafe id (schema rejects),
    but defense in depth: bypass-construct via the dataclass-style attribute
    override and confirm the provisioner refuses to derive a path.

    We use SimpleNamespace as a stand-in for ManifestService to bypass the
    schema validator and confirm the provisioner-internal check fires.
    """
    from types import SimpleNamespace

    from jig.dev_env.ephemeral import SqliteEphemeralProvisioner

    p = SqliteEphemeralProvisioner(project_root=tmp_path)
    bad_service = SimpleNamespace(
        id="../escape",
        namespace_template="{agent_id}_{ticket_id}",
    )
    with pytest.raises(ValueError):
        p._db_path(
            bad_service,  # type: ignore[arg-type]
            agent_id="a",
            ticket_id="t",
        )


# ---------------------------------------------------------------------------
# worktree — ticket_id propagated into worktree path + git ref
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_worktree_rejects_unsafe_ticket_id(tmp_path: Path) -> None:
    from jig.worktree import create_worktree

    with pytest.raises(ValueError):
        await create_worktree(
            project_path=tmp_path,
            ticket_id="../escape",
            base_branch="main",
        )


@pytest.mark.asyncio
async def test_remove_worktree_rejects_unsafe_ticket_id(tmp_path: Path) -> None:
    from jig.worktree import remove_worktree

    with pytest.raises(ValueError):
        await remove_worktree(project_path=tmp_path, ticket_id="../escape")


# ---------------------------------------------------------------------------
# reviewers — _default_worktree_path
# ---------------------------------------------------------------------------


def test_contract_compliance_default_worktree_rejects_unsafe(tmp_path: Path) -> None:
    from jig.reviewers.contract_compliance import _default_worktree_path

    with pytest.raises(ValueError):
        _default_worktree_path(tmp_path, "../escape")


def test_visual_compliance_default_worktree_rejects_unsafe(tmp_path: Path) -> None:
    from jig.reviewers.visual_compliance import _default_worktree_path

    with pytest.raises(ValueError):
        _default_worktree_path(tmp_path, "../escape")
