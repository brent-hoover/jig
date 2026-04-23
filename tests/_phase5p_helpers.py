"""Shared scaffolding for Phase 5 Task P integration tests.

Each test configures phases/roles/checks, then calls ``build_orch``
to get an ``Orchestrator`` wired with in-memory stores and stubs
that bypass the real git/worktree paths.
"""

from __future__ import annotations

from pathlib import Path

from jig.models import RoleConfig, WorkflowConfig
from jig.orchestrator import Orchestrator
from jig.persistence import save_role, save_workflow
from jig.project import Project, save_project


def build_orch(
    tmp_path: Path,
    *,
    workflow: WorkflowConfig,
    roles: list[RoleConfig],
    checks_yaml: str | None = None,
    monkeypatch,
) -> Orchestrator:
    """Scaffold a project on ``tmp_path`` and return an
    ``Orchestrator`` with worktree/merge/remove stubbed.

    * ``workflow`` — pre-built ``WorkflowConfig`` (caller chooses
      phases/evaluators so each test controls its scenario).
    * ``roles`` — every role referenced by the workflow. Missing
      role files blow up at evaluator spawn time.
    * ``checks_yaml`` — raw YAML body for ``.jig/checks.yaml``;
      None means no catalog (automated_checks must also be empty).
    * ``monkeypatch`` — pytest fixture; patches are applied on the
      ``jig.worktree`` module for ``merge_ticket`` / ``remove_worktree``.

    The caller is still responsible for ``monkeypatch.setattr(
    orch_module, "run_agent", ...)`` and for calling
    ``orch.startup()`` / ``orch.shutdown()``.
    """
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )

    (tmp_path / ".jig" / "workflows").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".jig" / "roles").mkdir(exist_ok=True)
    save_workflow(tmp_path, workflow)
    for role in roles:
        save_role(tmp_path, role)

    if checks_yaml is not None:
        (tmp_path / ".jig" / "checks.yaml").write_text(checks_yaml)

    orch = Orchestrator(project_path=tmp_path)

    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir(exist_ok=True)

    async def fake_ensure(ticket):
        return worktree_dir

    orch._ensure_worktree = fake_ensure  # type: ignore[method-assign]

    async def fake_merge(*args, **kwargs):
        return "stub-merge"

    async def fake_remove(*args, **kwargs):
        return None

    monkeypatch.setattr("jig.worktree.merge_ticket", fake_merge)
    monkeypatch.setattr("jig.worktree.remove_worktree", fake_remove)

    return orch


async def poll_until(predicate, *, timeout_s: float = 5.0, step_s: float = 0.05) -> bool:
    """Poll ``predicate`` every ``step_s`` seconds until it returns
    truthy or ``timeout_s`` elapses. Returns the final truthiness —
    callers assert on a specific condition, so ``True`` means the
    condition was met before timeout.
    """
    import asyncio

    steps = max(1, int(timeout_s / step_s))
    for _ in range(steps):
        if await predicate():
            return True
        await asyncio.sleep(step_s)
    return bool(await predicate())
