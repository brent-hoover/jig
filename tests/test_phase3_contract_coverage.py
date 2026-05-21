"""Phase 3 — ContractTestCoverageReviewer tests.

Covers:
  - No-op when architecture.yaml is absent
  - No-op when no module has consumes_apis / consumes_events
  - Flags API consumption with no integration_ac mention on either side
  - Flags event consumption with no integration_ac mention on either side
  - No flag when provider's integration_ac mentions the API name
  - No flag when consumer's integration_ac mentions the API name
  - Case-insensitive substring match
  - CONTRACT_TEST_COVERAGE_GAP wired into ReviewerCommentType
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---- helpers ----------------------------------------------------------------


def _write_arch_yaml(project_root: Path, arch_dict: dict) -> None:
    p = project_root / ".jig" / "spec" / "architecture.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(arch_dict, allow_unicode=True))


def _write_contracts_yaml(
    project_root: Path, module_id: str, contracts_dict: dict
) -> None:
    p = project_root / ".jig" / "spec" / "modules" / module_id / "contracts.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(contracts_dict, allow_unicode=True))


def _minimal_intent() -> dict:
    return {
        "problem": "Need to do X.",
        "simplest_solution": "One function that does X.",
        "complications_considered": {},
    }


def _make_ticket() -> Ticket:
    return Ticket(
        id="t-01",
        title="Test ticket",
        work_type=WorkType.FEATURE,
        created_by="pm",
        description=TICKET_AC_PLACEHOLDER,
    )


def _minimal_arch(*, consumer_apis=None, consumer_events=None) -> dict:
    return {
        "spec_version": 1,
        "data_stores": [{"id": "main-db", "kind": "postgres"}],
        "modules": [
            {
                "id": "provider",
                "title": "Provider",
                "summary": "Provides stuff.",
                "intent": _minimal_intent(),
                "n_a_categories": ["behavioral_contracts", "external_dependencies"],
            },
            {
                "id": "consumer",
                "title": "Consumer",
                "summary": "Consumes stuff.",
                "intent": _minimal_intent(),
                "n_a_categories": [
                    "behavioral_contracts",
                    "external_dependencies",
                    "ownership",
                ],
                "consumes_apis": consumer_apis or [],
                "consumes_events": consumer_events or [],
            },
        ],
    }


def _minimal_contracts(module_id: str, *, integration_ac=None) -> dict:
    return {
        "spec_version": 1,
        "module": module_id,
        "owns": [],
        "integration_ac": integration_ac or [],
        "exposes": [{"name": "get_data", "kind": "function", "summary": "Get data."}],
        "emits": [{"name": "data.created", "summary": "Data created."}],
    }


# ---- enum value wired -------------------------------------------------------


def test_comment_type_has_contract_test_coverage_gap():
    from jig.reviewers.comment import ReviewerCommentType

    assert (
        ReviewerCommentType.CONTRACT_TEST_COVERAGE_GAP.value
        == "contract-test-coverage-gap"
    )


# ---- no-op cases ------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_arch_yaml_returns_empty(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer

    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)
    assert comments == []


@pytest.mark.asyncio
async def test_no_consumptions_returns_empty(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer

    _write_arch_yaml(tmp_path, _minimal_arch())
    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)
    assert comments == []


# ---- API consumption gap flagged --------------------------------------------


@pytest.mark.asyncio
async def test_api_consumption_both_sides_silent_flags_gap(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer
    from jig.reviewers.comment import ReviewerCommentType, Severity

    _write_arch_yaml(
        tmp_path,
        _minimal_arch(
            consumer_apis=[{"module": "provider", "name": "get_data"}],
        ),
    )
    # Neither side mentions get_data in integration_ac
    _write_contracts_yaml(
        tmp_path,
        "provider",
        _minimal_contracts(
            "provider",
            integration_ac=[
                {"capability": "cap-a", "must": ["does something unrelated"]}
            ],
        ),
    )
    _write_contracts_yaml(
        tmp_path,
        "consumer",
        _minimal_contracts(
            "consumer",
            integration_ac=[{"capability": "cap-b", "must": ["calls something else"]}],
        ),
    )

    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)

    assert len(comments) == 1
    c = comments[0]
    assert c.type == ReviewerCommentType.CONTRACT_TEST_COVERAGE_GAP.value
    assert c.severity == Severity.NOTABLE.value
    assert "get_data" in c.prose
    assert "provider" in c.prose
    assert "consumer" in c.prose


@pytest.mark.asyncio
async def test_api_consumption_provider_covers_no_flag(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer

    _write_arch_yaml(
        tmp_path,
        _minimal_arch(
            consumer_apis=[{"module": "provider", "name": "get_data"}],
        ),
    )
    _write_contracts_yaml(
        tmp_path,
        "provider",
        _minimal_contracts(
            "provider",
            integration_ac=[
                {"capability": "cap-a", "must": ["exposes get_data to callers"]}
            ],
        ),
    )
    _write_contracts_yaml(tmp_path, "consumer", _minimal_contracts("consumer"))

    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)
    assert comments == []


@pytest.mark.asyncio
async def test_api_consumption_consumer_covers_no_flag(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer

    _write_arch_yaml(
        tmp_path,
        _minimal_arch(
            consumer_apis=[{"module": "provider", "name": "get_data"}],
        ),
    )
    _write_contracts_yaml(tmp_path, "provider", _minimal_contracts("provider"))
    _write_contracts_yaml(
        tmp_path,
        "consumer",
        _minimal_contracts(
            "consumer",
            integration_ac=[
                {"capability": "cap-b", "must": ["calls provider get_data"]}
            ],
        ),
    )

    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)
    assert comments == []


# ---- event consumption gap flagged ------------------------------------------


@pytest.mark.asyncio
async def test_event_consumption_both_sides_silent_flags_gap(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer
    from jig.reviewers.comment import ReviewerCommentType

    _write_arch_yaml(
        tmp_path,
        _minimal_arch(
            consumer_events=[{"module": "provider", "name": "data.created"}],
        ),
    )
    _write_contracts_yaml(
        tmp_path,
        "provider",
        _minimal_contracts(
            "provider",
            integration_ac=[{"capability": "cap-a", "must": ["does something"]}],
        ),
    )
    _write_contracts_yaml(
        tmp_path,
        "consumer",
        _minimal_contracts(
            "consumer",
            integration_ac=[{"capability": "cap-b", "must": ["unrelated must"]}],
        ),
    )

    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)

    assert len(comments) == 1
    c = comments[0]
    assert c.type == ReviewerCommentType.CONTRACT_TEST_COVERAGE_GAP.value
    assert "data.created" in c.prose


@pytest.mark.asyncio
async def test_event_consumption_provider_covers_no_flag(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer

    _write_arch_yaml(
        tmp_path,
        _minimal_arch(
            consumer_events=[{"module": "provider", "name": "data.created"}],
        ),
    )
    _write_contracts_yaml(
        tmp_path,
        "provider",
        _minimal_contracts(
            "provider",
            integration_ac=[
                {"capability": "cap-a", "must": ["emits data.created event"]}
            ],
        ),
    )
    _write_contracts_yaml(tmp_path, "consumer", _minimal_contracts("consumer"))

    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)
    assert comments == []


# ---- case-insensitive match -------------------------------------------------


@pytest.mark.asyncio
async def test_match_is_case_insensitive(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer

    _write_arch_yaml(
        tmp_path,
        _minimal_arch(
            consumer_apis=[{"module": "provider", "name": "GetData"}],
        ),
    )
    _write_contracts_yaml(
        tmp_path,
        "provider",
        _minimal_contracts(
            "provider",
            integration_ac=[
                {"capability": "cap-a", "must": ["EXPOSES getdata TO ALL"]}
            ],
        ),
    )
    _write_contracts_yaml(tmp_path, "consumer", _minimal_contracts("consumer"))

    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)
    assert comments == []


# ---- multiple gaps -----------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_gaps_reported(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer

    arch = _minimal_arch(
        consumer_apis=[
            {"module": "provider", "name": "api_one"},
            {"module": "provider", "name": "api_two"},
        ],
        consumer_events=[{"module": "provider", "name": "evt.fired"}],
    )
    _write_arch_yaml(tmp_path, arch)
    _write_contracts_yaml(tmp_path, "provider", _minimal_contracts("provider"))
    _write_contracts_yaml(tmp_path, "consumer", _minimal_contracts("consumer"))

    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)
    assert len(comments) == 3


# ---- missing contracts file is tolerated ------------------------------------


@pytest.mark.asyncio
async def test_missing_contracts_file_tolerated(tmp_path):
    from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer

    _write_arch_yaml(
        tmp_path,
        _minimal_arch(
            consumer_apis=[{"module": "provider", "name": "get_data"}],
        ),
    )
    # Neither module has a contracts.yaml — should flag the gap (neither side covers)
    reviewer = ContractTestCoverageReviewer()
    comments = await reviewer.review(_make_ticket(), tmp_path)
    assert len(comments) == 1


# ---- dispatch wiring --------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_includes_contract_test_coverage_at_end_of_ticket(tmp_path):
    from jig.reviewers.dispatch import (
        dispatch_for_cadence,
        CONTRACT_TEST_COVERAGE_REVIEWER_ID,
    )

    ticket = Ticket(
        id="t-dispatch",
        title="Dispatch test",
        work_type=WorkType.FEATURE,
        created_by="pm",
        layer="mvp",
        description=TICKET_AC_PLACEHOLDER,
    )
    result = await dispatch_for_cadence(ticket, tmp_path, "end_of_ticket")
    assert CONTRACT_TEST_COVERAGE_REVIEWER_ID in result


@pytest.mark.asyncio
async def test_dispatch_excludes_contract_test_coverage_at_per_commit(tmp_path):
    from jig.reviewers.dispatch import (
        dispatch_for_cadence,
        CONTRACT_TEST_COVERAGE_REVIEWER_ID,
    )

    ticket = Ticket(
        id="t-dispatch2",
        title="Dispatch test",
        work_type=WorkType.FEATURE,
        created_by="pm",
        layer="mvp",
        description=TICKET_AC_PLACEHOLDER,
    )
    result = await dispatch_for_cadence(ticket, tmp_path, "per_commit")
    assert CONTRACT_TEST_COVERAGE_REVIEWER_ID not in result
