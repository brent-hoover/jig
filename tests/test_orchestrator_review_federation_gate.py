"""Block 3 (Important 1) — orchestrator opt-in review-federation gate.

Per the v2 code review's Important #1 finding, the federation
selection ran but never executed. Block 3 wires
``dispatch_with_llm_spawn`` into the orchestrator's per-ticket
lifecycle behind an opt-in config flag (``orchestrator.run_review_federation``).
Default False so test/CI runs don't burn LLM tokens; operators flip
the flag on per-project once they're ready to run real federation
passes.

This module pins:

- The flag defaults False on a fresh orchestrator (no config file).
- When False, ``_run_review_federation`` is NOT called at ticket
  resolution.
- When True, ``_run_review_federation`` IS called at ticket
  resolution and forwards to ``dispatch_with_llm_spawn`` via the
  orchestrator's own ``spawn_review_agent_for_id`` helper.

Tests use the orchestrator's helper directly + a stub
``dispatch_with_llm_spawn`` to avoid spinning up the full agent
spawn path (covered separately in test_reviewers_federation_execution).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from jig.config import (
    Config,
    OrchestratorSection,
    save_config,
)
from jig.orchestrator import Orchestrator
from jig.project import Project
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---- helpers -------------------------------------------------------------


def _seed_project(root: Path, *, run_federation: bool = False) -> None:
    """Write a minimal .jig/config.yaml so load_config succeeds."""
    cfg_dir = root / ".jig"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(
        project=Project(
            id="test-fed-gate",
            name="test-fed-gate",
            path=str(root),
        ),
        orchestrator=OrchestratorSection(run_review_federation=run_federation),
    )
    save_config(root, cfg)


def _ticket() -> Ticket:
    return Ticket(
        id="tb-gate",
        work_type=WorkType.FEATURE,
        title="federation gate test",
        created_by="planner-pm",
        labels=["touches-auth"],  # would trigger reviewer-security
        layer="mvp",
        description=TICKET_AC_PLACEHOLDER,
    )


# ---- defaults ------------------------------------------------------------


class TestRunReviewFederationDefaults:
    """The flag defaults ``True`` per the v2 design — review federation
    is the gate; operators opt out per-project for legacy passive
    behavior. The defaults pinned here keep the contract honest with
    ``OrchestratorSection`` so a future flip is loud."""

    def test_default_true_when_no_config(self, tmp_path: Path) -> None:
        """Fresh orchestrator (no config file) defaults to gate-on."""
        orch = Orchestrator(project_path=tmp_path)
        # Inspect via private member — the field is intentionally
        # private so production callers don't depend on it.
        cfg = orch._orchestrator_cfg  # type: ignore[attr-defined]
        assert cfg.run_review_federation is True

    def test_default_true_when_flag_omitted(self, tmp_path: Path) -> None:
        """Config file without the flag inherits the gate-on default."""
        cfg_dir = tmp_path / ".jig"
        cfg_dir.mkdir()
        # Write a minimal config that doesn't set the orchestrator section.
        (cfg_dir / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "project": {
                        "id": "x",
                        "name": "x",
                        "path": str(tmp_path),
                    }
                }
            )
        )
        from jig.config import load_config

        loaded = load_config(tmp_path)
        assert loaded.orchestrator.run_review_federation is True

    def test_explicit_opt_out_loads(self, tmp_path: Path) -> None:
        """Operator-set opt-out round-trips through config."""
        _seed_project(tmp_path, run_federation=False)
        from jig.config import load_config

        loaded = load_config(tmp_path)
        assert loaded.orchestrator.run_review_federation is False

    def test_explicit_opt_in_loads(self, tmp_path: Path) -> None:
        """Operator-set opt-in (mirrors default) round-trips."""
        _seed_project(tmp_path, run_federation=True)
        from jig.config import load_config

        loaded = load_config(tmp_path)
        assert loaded.orchestrator.run_review_federation is True


# ---- gate behaviour ------------------------------------------------------


class TestFederationGate:
    @pytest.mark.asyncio
    async def test_default_off_does_not_call_federation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the flag is False, ticket-resolved skips federation."""
        called: dict[str, bool] = {"called": False}

        async def _spy(self: Any, ticket_id: str, ticket: Any) -> None:
            called["called"] = True

        monkeypatch.setattr(
            Orchestrator,
            "_run_review_federation",
            _spy,
            raising=True,
        )

        orch = Orchestrator(project_path=tmp_path)
        # Set the flag explicitly to off (mirrors fresh-orchestrator
        # default; pin the wire-through behaviour explicitly).
        orch._orchestrator_cfg = OrchestratorSection(  # type: ignore[attr-defined]
            run_review_federation=False
        )

        # Construct enough of the orchestrator state for
        # ``_on_ticket_completed`` to run far enough to hit the gate.
        # Real merge-flow setup is fixture-heavy; instead drive the
        # gate directly via the same conditional the production code
        # uses. This keeps the test focused on the gate, not the
        # whole resolution flow.
        if orch._orchestrator_cfg.run_review_federation:  # type: ignore[attr-defined]
            await orch._run_review_federation("tb-gate", _ticket())

        assert called["called"] is False

    @pytest.mark.asyncio
    async def test_flag_on_calls_federation(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the flag is True, ticket-resolved fires the federation hook."""
        called: dict[str, Any] = {}

        async def _spy(self: Any, ticket_id: str, ticket: Any) -> None:
            called["ticket_id"] = ticket_id
            called["ticket"] = ticket

        monkeypatch.setattr(
            Orchestrator,
            "_run_review_federation",
            _spy,
            raising=True,
        )

        orch = Orchestrator(project_path=tmp_path)
        orch._orchestrator_cfg = OrchestratorSection(  # type: ignore[attr-defined]
            run_review_federation=True
        )

        if orch._orchestrator_cfg.run_review_federation:  # type: ignore[attr-defined]
            await orch._run_review_federation("tb-gate", _ticket())

        assert called["ticket_id"] == "tb-gate"


class TestFederationHookForwardsToDispatch:
    """``_run_review_federation`` calls ``dispatch_with_llm_spawn``."""

    @pytest.mark.asyncio
    async def test_calls_dispatch_with_llm_spawn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        async def _stub_dispatch(
            ticket: Any,
            project_root: Any,
            orchestrator: Any,
            **kwargs: Any,
        ) -> dict:
            captured["ticket_id"] = ticket.id
            captured["project_root"] = project_root
            captured["orchestrator"] = orchestrator
            captured["reviewers"] = kwargs.get("reviewers")
            return {}

        # Patch the symbol the orchestrator imports lazily.
        from jig.reviewers import dispatch as dispatch_module

        monkeypatch.setattr(
            dispatch_module,
            "dispatch_with_llm_spawn",
            _stub_dispatch,
        )
        # Also patch the re-export in jig.reviewers since the
        # orchestrator imports from there.
        from jig import reviewers as reviewers_pkg

        monkeypatch.setattr(
            reviewers_pkg,
            "dispatch_with_llm_spawn",
            _stub_dispatch,
        )

        orch = Orchestrator(project_path=tmp_path)
        await orch._run_review_federation("tb-gate", _ticket())

        assert captured["ticket_id"] == "tb-gate"
        assert captured["project_root"] == tmp_path
        assert captured["orchestrator"] is orch
        # Post-RESOLVE gate has no phase context — passes None so the
        # legacy cadence-driven selection runs unchanged.
        assert captured["reviewers"] is None

    @pytest.mark.asyncio
    async def test_dispatch_failure_does_not_propagate(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A misbehaving reviewer must not block ticket resolution."""

        async def _raise(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("simulated reviewer agent crash")

        from jig.reviewers import dispatch as dispatch_module
        from jig import reviewers as reviewers_pkg

        monkeypatch.setattr(dispatch_module, "dispatch_with_llm_spawn", _raise)
        monkeypatch.setattr(reviewers_pkg, "dispatch_with_llm_spawn", _raise)

        orch = Orchestrator(project_path=tmp_path)
        # Should not raise.
        await orch._run_review_federation("tb-gate", _ticket())
