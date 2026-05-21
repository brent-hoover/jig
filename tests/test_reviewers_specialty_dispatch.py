"""Specialty-reviewer auto-selection (Track G Final).

Per ``docs/v2.0/pm-workflow/design.md`` §"Reviewer federation — selection
logic", three specialty reviewers join the federation when the
ticket's characteristics call for them:

- security — labels touch auth/PII/secrets/payments OR module is SA-tier
- performance — ``perf-budget`` label OR linked AC mentions perf budget
- architectural — ``touches-contract`` label OR ``dev_tier == "sa"``
  OR ``contract_amendment`` populated

Selection is additive — a planner-authored ``reviewer_set`` never
suppresses a specialty reviewer. The role-config registration tests
live alongside (mirroring the existing judgment-reviewer pattern in
``test_reviewer_mcp.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.intent import Intent
from jig.persistence import load_role
from jig.reviewers import (
    ARCHITECTURAL_REVIEWER_ID,
    BONES_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    INTENT_REVIEWER_ID,
    PERFORMANCE_REVIEWER_ID,
    SECURITY_REVIEWER_ID,
    SPEC_COMPLIANCE_REVIEWER_ID,
    select_reviewers_for_ticket,
)
from jig.schemas.arch import (
    Architecture,
    ContractsFile,
    IntegrationAcceptance,
    Module,
    TierHint,
)
from jig.spec_loader import architecture_path, module_contracts_path
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---- helpers -------------------------------------------------------------


def _intent() -> Intent:
    return Intent(
        problem="Specialty-dispatch tests need a parseable architecture.",
        simplest_solution="Hand-write modules with the tier_hint we want to test.",
    )


def _write_arch(
    project_root: Path,
    *,
    modules: list[Module] | None = None,
) -> None:
    arch = Architecture(
        modules=modules
        or [
            Module(
                id="catalog-ingest",
                title="Catalog Ingest",
                summary="Test module.",
                tier_hint=TierHint.STANDARD,
                intent=_intent(),
            )
        ],
    )
    path = architecture_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(arch.model_dump(mode="json")))


def _write_contracts(
    project_root: Path,
    *,
    module_id: str = "catalog-ingest",
    integration_ac: list[IntegrationAcceptance] | None = None,
) -> None:
    contracts = ContractsFile(
        spec_version=1,
        module=module_id,
        integration_ac=integration_ac
        or [
            IntegrationAcceptance(
                capability="shopify-connect",
                must=["Shopify catalog flows into products collection"],
            )
        ],
    )
    path = module_contracts_path(project_root, module_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(contracts.model_dump(mode="json")))


def _ticket(
    *,
    layer: str | None = "mvp",
    labels: list[str] | None = None,
    dev_tier: str | None = None,
    contract_amendment: str | None = None,
    capability_ids: list[str] | None = None,
    reviewer_set: list[str] | None = None,
    module_id: str | None = "catalog-ingest",
) -> Ticket:
    return Ticket(
        id="t-spec",
        work_type=WorkType.FEATURE,
        title="dispatch-specialty test",
        created_by="planner-pm",
        module_id=module_id,
        capability_ids=(
            capability_ids if capability_ids is not None else ["shopify-connect"]
        ),
        layer=layer,
        labels=labels or [],
        dev_tier=dev_tier,
        contract_amendment=contract_amendment,
        reviewer_set=reviewer_set or [],
        description=TICKET_AC_PLACEHOLDER,
    )


# ---- role-config registration -------------------------------------------


class TestSpecialtyRoleConfigsLoad:
    """Mirrors ``test_reviewer_mcp.TestRoleConfigsLoad`` for the three
    Track G Final specialty roles."""

    def test_security_loads(self, tmp_path: Path) -> None:
        cfg = load_role(tmp_path, "reviewer_security")
        assert cfg.role == "reviewer-security"
        assert "reviewer_post_comment" in cfg.allowed_tools
        assert cfg.strict_tools is True

    def test_performance_loads(self, tmp_path: Path) -> None:
        cfg = load_role(tmp_path, "reviewer_performance")
        assert cfg.role == "reviewer-performance"
        assert "reviewer_post_comment" in cfg.allowed_tools
        assert cfg.strict_tools is True

    def test_architectural_loads(self, tmp_path: Path) -> None:
        cfg = load_role(tmp_path, "reviewer_architectural")
        assert cfg.role == "reviewer-architectural"
        assert "reviewer_post_comment" in cfg.allowed_tools
        assert cfg.strict_tools is True


# ---- security selection --------------------------------------------------


class TestSecuritySelection:
    @pytest.mark.parametrize(
        "label",
        [
            "touches-auth",
            "touches-pii",
            "touches-secrets",
            "touches-payments",
        ],
    )
    def test_label_triggers_security(self, label: str) -> None:
        ids = select_reviewers_for_ticket(_ticket(labels=[label]))
        assert SECURITY_REVIEWER_ID in ids

    def test_no_security_label_no_select(self) -> None:
        ids = select_reviewers_for_ticket(_ticket(labels=[]))
        assert SECURITY_REVIEWER_ID not in ids

    def test_unrelated_label_does_not_trigger(self) -> None:
        ids = select_reviewers_for_ticket(_ticket(labels=["good-first-issue"]))
        assert SECURITY_REVIEWER_ID not in ids

    def test_sa_tier_module_triggers_security(self, tmp_path: Path) -> None:
        _write_arch(
            tmp_path,
            modules=[
                Module(
                    id="catalog-ingest",
                    title="Catalog Ingest",
                    summary="SA-tier module under test.",
                    tier_hint=TierHint.SA,
                    intent=_intent(),
                )
            ],
        )
        ids = select_reviewers_for_ticket(_ticket(), project_root=tmp_path)
        assert SECURITY_REVIEWER_ID in ids

    def test_standard_tier_module_does_not_trigger(self, tmp_path: Path) -> None:
        _write_arch(tmp_path)  # default STANDARD
        ids = select_reviewers_for_ticket(_ticket(), project_root=tmp_path)
        assert SECURITY_REVIEWER_ID not in ids

    def test_security_added_on_top_of_planner_set(self) -> None:
        """A planner-authored set must NOT suppress security selection."""
        ids = select_reviewers_for_ticket(
            _ticket(
                reviewer_set=[BONES_REVIEWER_ID],
                labels=["touches-auth"],
            )
        )
        assert BONES_REVIEWER_ID in ids
        assert SECURITY_REVIEWER_ID in ids


# ---- performance selection -----------------------------------------------


class TestPerformanceSelection:
    def test_perf_budget_label_triggers(self) -> None:
        ids = select_reviewers_for_ticket(_ticket(labels=["perf-budget"]))
        assert PERFORMANCE_REVIEWER_ID in ids

    @pytest.mark.parametrize(
        "must_text",
        [
            "MUST serve under 200 ms p99",
            "Throughput must exceed 100 RPS",
            "Latency under 500 ms required",
            "Handles 1000 requests per second sustained",
            "p95 latency below 50ms",
        ],
    )
    def test_ac_keyword_triggers(self, tmp_path: Path, must_text: str) -> None:
        _write_arch(tmp_path)
        _write_contracts(
            tmp_path,
            integration_ac=[
                IntegrationAcceptance(capability="shopify-connect", must=[must_text])
            ],
        )
        ids = select_reviewers_for_ticket(_ticket(), project_root=tmp_path)
        assert PERFORMANCE_REVIEWER_ID in ids, (
            f"Expected perf reviewer to fire on AC text {must_text!r}"
        )

    def test_no_perf_signal_no_select(self, tmp_path: Path) -> None:
        _write_arch(tmp_path)
        _write_contracts(
            tmp_path,
            integration_ac=[
                IntegrationAcceptance(
                    capability="shopify-connect",
                    must=["catalog rows persist correctly"],
                )
            ],
        )
        ids = select_reviewers_for_ticket(_ticket(), project_root=tmp_path)
        assert PERFORMANCE_REVIEWER_ID not in ids

    def test_no_project_root_only_label_works(self) -> None:
        """Without project_root, only the label trigger fires (no AC scan)."""
        ids = select_reviewers_for_ticket(_ticket(labels=["perf-budget"]))
        assert PERFORMANCE_REVIEWER_ID in ids

        ids = select_reviewers_for_ticket(_ticket())
        assert PERFORMANCE_REVIEWER_ID not in ids

    def test_capability_scoping_filters_out_non_relevant_ac(
        self, tmp_path: Path
    ) -> None:
        """AC scan respects capability_ids — perf AC on a different
        capability must NOT trigger the reviewer for our ticket."""
        _write_arch(tmp_path)
        _write_contracts(
            tmp_path,
            integration_ac=[
                IntegrationAcceptance(
                    capability="other-capability",
                    must=["MUST serve under 200 ms p99"],
                ),
                IntegrationAcceptance(
                    capability="shopify-connect",
                    must=["catalog rows persist"],
                ),
            ],
        )
        ids = select_reviewers_for_ticket(
            _ticket(capability_ids=["shopify-connect"]),
            project_root=tmp_path,
        )
        assert PERFORMANCE_REVIEWER_ID not in ids


# ---- architectural selection ---------------------------------------------


class TestArchitecturalSelection:
    def test_touches_contract_label_triggers(self) -> None:
        ids = select_reviewers_for_ticket(_ticket(labels=["touches-contract"]))
        assert ARCHITECTURAL_REVIEWER_ID in ids

    def test_sa_dev_tier_triggers(self) -> None:
        ids = select_reviewers_for_ticket(_ticket(dev_tier="sa"))
        assert ARCHITECTURAL_REVIEWER_ID in ids

    def test_contract_amendment_triggers(self) -> None:
        ids = select_reviewers_for_ticket(
            _ticket(contract_amendment="adds optional retry field")
        )
        assert ARCHITECTURAL_REVIEWER_ID in ids

    def test_standard_tier_no_amendment_no_select(self) -> None:
        ids = select_reviewers_for_ticket(_ticket(dev_tier="standard"))
        assert ARCHITECTURAL_REVIEWER_ID not in ids

    def test_senior_tier_alone_does_not_trigger(self) -> None:
        ids = select_reviewers_for_ticket(_ticket(dev_tier="senior"))
        assert ARCHITECTURAL_REVIEWER_ID not in ids


# ---- combined behaviour --------------------------------------------------


class TestSpecialtyAdditive:
    def test_specialties_compose_with_mvp_defaults(self, tmp_path: Path) -> None:
        """A perf-budget MVP ticket gets the four mechanical defaults
        AND the perf reviewer, no overlaps."""
        ids = select_reviewers_for_ticket(_ticket(labels=["perf-budget"]))
        # MVP defaults present.
        assert BONES_REVIEWER_ID in ids
        assert INTENT_REVIEWER_ID in ids
        assert CROSS_CUTTING_REVIEWER_ID in ids
        assert SPEC_COMPLIANCE_REVIEWER_ID in ids
        # Specialty added.
        assert PERFORMANCE_REVIEWER_ID in ids
        # No duplicates.
        assert len(ids) == len(set(ids))

    def test_specialties_fire_when_layer_unset(self) -> None:
        """A SA-amendment ticket without a layer must still select the
        architectural reviewer — the bones-era empty-list branch must
        not suppress security/perf/arch."""
        ids = select_reviewers_for_ticket(
            _ticket(layer=None, contract_amendment="touches contract")
        )
        assert ARCHITECTURAL_REVIEWER_ID in ids

    def test_unrelated_ticket_yields_no_specialty(self, tmp_path: Path) -> None:
        """Vanilla MVP ticket with no triggers gets no specialty reviewers."""
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        ids = select_reviewers_for_ticket(_ticket(), project_root=tmp_path)
        assert SECURITY_REVIEWER_ID not in ids
        assert PERFORMANCE_REVIEWER_ID not in ids
        assert ARCHITECTURAL_REVIEWER_ID not in ids


# ---- contract_amendment field on Ticket ---------------------------------


class TestContractAmendmentField:
    def test_default_is_none(self) -> None:
        t = Ticket(
            id="t",
            work_type=WorkType.FEATURE,
            title="x",
            created_by="po",
            description=TICKET_AC_PLACEHOLDER,
        )
        assert t.contract_amendment is None

    def test_round_trip(self) -> None:
        t = Ticket(
            id="t",
            work_type=WorkType.FEATURE,
            title="x",
            created_by="po",
            contract_amendment="extends ingest contract",
            description=TICKET_AC_PLACEHOLDER,
        )
        payload = t.model_dump(mode="json")
        restored = Ticket.model_validate(payload)
        assert restored.contract_amendment == "extends ingest contract"
