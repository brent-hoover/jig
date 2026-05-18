"""ContractTestCoverageReviewer — flags contracts with consumers but no test.

Phase 3 item #6. Mechanical (no LLM). Reads the authored architecture + every
module's contracts.yaml and checks:

  For each ApiConsumption declared by any module:
    - Does the provider's integration_ac mention the API name?
    - Does the consumer's integration_ac mention the API name?
  If neither: flag as notable ("consumer-driven contract gap").

  Same check for EventConsumption vs. the provider's EmittedEvent name.

"Mention" is a case-insensitive substring match against the ``must`` strings
in every IntegrationAcceptance entry. A full provider-test / consumer-test
framework is out of scope for MVP; the goal is to make the gap *visible* so
the agent knows to add an integration AC entry before the ticket resolves.

Fires at end-of-ticket cadence. No-ops when:
  - No architecture.yaml exists.
  - No module has consumes_apis or consumes_events declared.
  - A contract already has adequate test coverage (both-side match).
"""

from __future__ import annotations

from pathlib import Path

from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.ticket import Ticket

REVIEWER_ID = "contract-test-coverage"


def _integration_ac_mentions(
    contracts_by_module: dict, module_id: str, name: str
) -> bool:
    """Return True if module_id's integration_ac has any must-string mentioning name."""
    cf = contracts_by_module.get(module_id)
    if cf is None:
        return False
    needle = name.lower()
    for iac in cf.integration_ac:
        for must_item in iac.must:
            if needle in must_item.lower():
                return True
    return False


class ContractTestCoverageReviewer:
    """Flags consumed contracts that have no provider or consumer integration test."""

    async def review(
        self,
        ticket: Ticket,
        project_root: Path,
    ) -> list[ReviewerComment]:
        # Load arch + per-module contracts lazily (only when arch exists).
        arch_path = project_root / ".jig" / "spec" / "architecture.yaml"
        if not arch_path.exists():
            return []

        from jig.spec_loader import load_architecture, load_module_contracts

        try:
            arch = load_architecture(project_root)
        except Exception:
            return []

        # Build a contracts map for all modules that have a contracts.yaml.
        contracts_by_module: dict[str, object] = {}
        for module in arch.modules:
            try:
                cf = load_module_contracts(project_root, module.id)
                contracts_by_module[module.id] = cf
            except Exception:
                pass

        comments: list[ReviewerComment] = []

        for module in arch.modules:
            for api_cons in module.consumes_apis:
                provider_covers = _integration_ac_mentions(
                    contracts_by_module, api_cons.module, api_cons.name
                )
                consumer_covers = _integration_ac_mentions(
                    contracts_by_module, module.id, api_cons.name
                )
                if not provider_covers and not consumer_covers:
                    comments.append(
                        ReviewerComment(
                            type=ReviewerCommentType.CONTRACT_TEST_COVERAGE_GAP,
                            severity=Severity.NOTABLE,
                            reviewer=REVIEWER_ID,
                            prose=(
                                f"Module '{module.id}' consumes '{api_cons.name}' from "
                                f"'{api_cons.module}', but neither module's integration_ac "
                                f"mentions '{api_cons.name}'. Add a MUST entry on the "
                                f"provider ('{api_cons.module}') or consumer ('{module.id}') "
                                "to make this contract testable."
                            ),
                            contract_uri=(
                                f"project://spec/modules/{api_cons.module}/contracts"
                                f"#exposes/{api_cons.name}"
                            ),
                        )
                    )

            for ev_cons in module.consumes_events:
                provider_covers = _integration_ac_mentions(
                    contracts_by_module, ev_cons.module, ev_cons.name
                )
                consumer_covers = _integration_ac_mentions(
                    contracts_by_module, module.id, ev_cons.name
                )
                if not provider_covers and not consumer_covers:
                    comments.append(
                        ReviewerComment(
                            type=ReviewerCommentType.CONTRACT_TEST_COVERAGE_GAP,
                            severity=Severity.NOTABLE,
                            reviewer=REVIEWER_ID,
                            prose=(
                                f"Module '{module.id}' consumes event '{ev_cons.name}' from "
                                f"'{ev_cons.module}', but neither module's integration_ac "
                                f"mentions '{ev_cons.name}'. Add a MUST entry covering "
                                "this event subscription."
                            ),
                            contract_uri=(
                                f"project://spec/modules/{ev_cons.module}/contracts"
                                f"#emits/{ev_cons.name}"
                            ),
                        )
                    )

        return comments
