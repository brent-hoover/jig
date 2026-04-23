"""Tests for the Proposal MCP tool handlers (Phase 3 Task G).

Covers: propose → route → accept path, the self-certification guard in
both ``warn`` and ``blocked`` modes, spec-version bump on accept,
reject / refine state transitions, and list_proposals filtering.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.config import Config, RoleAssignment, RolesSection, save_config
from jig.persistence import init_project
from jig.project import Project
from jig.proposal_mcp import (
    ProposalError,
    handle_list_proposals,
    handle_propose_change,
    handle_resolve_proposal,
)
from jig.specs import TicketSpec, load_ticket_spec, save_ticket_spec
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff
from jig.ticket import Size, Ticket, WorkType


@pytest.fixture
async def project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    init_project(tmp_path)
    return tmp_path


@pytest.fixture
async def tickets(tmp_path: Path) -> TicketStore:
    store = TicketStore(tmp_path / ".jig" / "store" / "tickets.jsonl")
    await store.load()
    return store


@pytest.fixture
async def threads(tmp_path: Path) -> ThreadStore:
    store = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    await store.load()
    return store


async def _make_ticket(tickets: TicketStore) -> Ticket:
    t = Ticket(
        id="t-1",
        work_type=WorkType.FEATURE,
        size=Size.M,
        title="Build widget",
        description="",
        created_by="alice",
    )
    await tickets.create(t)
    return t


async def _make_spec(project: Path) -> None:
    save_ticket_spec(
        project,
        TicketSpec(
            ticket_id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            fields={
                "summary": "widget",
                "behaviors": [{"id": "B1", "when": "click", "then": "ok"}],
                "acceptance_criteria": ["B1 verified"],
                "out_of_scope": ["collab"],
            },
        ),
    )


def _cfg_with_roles(tmp_path: Path, *, self_approval: str = "warn") -> Config:
    return Config(
        project=Project(id="p", name="p", path=str(tmp_path)),
        roles=RolesSection(
            po=RoleAssignment(assignment="human", human="pam"),
            sa=RoleAssignment(assignment="human", human="sam"),
        ),
        self_approval=self_approval,
    )


# ---- propose / accept happy path ------------------------------------------


class TestHappyPath:
    async def test_propose_then_accept_bumps_spec(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "rewritten summary",
            },
            project_path=project,
        )
        assert propose["state"] == "pending"
        assert propose["routing"]["role"] == "po"
        assert propose["routing"]["assignee"] == "pam"

        # Different actor accepts.
        accept = await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="pam",
            args={
                "proposal_id": propose["comment_id"],
                "verdict": "accept",
            },
            project_path=project,
        )
        assert accept["state"] == "accepted"
        assert accept["self_approved"] is False
        assert accept["spec_version"] == 2

        spec = load_ticket_spec(project, "t-1")
        assert spec is not None
        assert spec.fields["summary"] == "rewritten summary"
        assert spec.version == 2

    async def test_reject_leaves_spec_untouched(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "nope",
            },
            project_path=project,
        )
        reject = await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="pam",
            args={
                "proposal_id": propose["comment_id"],
                "verdict": "reject",
            },
            project_path=project,
        )
        assert reject["state"] == "rejected"
        assert reject["spec_version"] is None
        spec = load_ticket_spec(project, "t-1")
        assert spec is not None
        assert spec.fields["summary"] == "widget"

    async def test_refine_state(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "maybe?",
            },
            project_path=project,
        )
        refine = await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="pam",
            args={
                "proposal_id": propose["comment_id"],
                "verdict": "refine",
                "reasoning": "more context please",
            },
            project_path=project,
        )
        assert refine["state"] == "refining"


# ---- self-certification guard ---------------------------------------------


class TestSelfApprovalWarn:
    async def test_self_accept_requires_reasoning(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project, self_approval="warn"))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "self-edit",
            },
            project_path=project,
        )
        # Same actor, no reasoning → refused.
        with pytest.raises(ProposalError, match="reasoning"):
            await handle_resolve_proposal(
                tickets=tickets,
                threads=threads,
                sender="alice",
                args={
                    "proposal_id": propose["comment_id"],
                    "verdict": "accept",
                },
                project_path=project,
            )

    async def test_self_accept_with_reasoning_records_marker(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project, self_approval="warn"))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "self-edit",
            },
            project_path=project,
        )
        accept = await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "proposal_id": propose["comment_id"],
                "verdict": "accept",
                "reasoning": "solo dev, no second reviewer available",
            },
            project_path=project,
        )
        assert accept["self_approved"] is True
        assert accept["state"] == "accepted"

        thread = await threads.for_ticket("t-1")
        markers = [
            e
            for e in thread
            if e.kind == "system_event"
            and getattr(e, "event_type", None) == "status_change"
            and "self_approval_with_justification" in getattr(e, "content", "")
        ]
        assert len(markers) == 1


class TestSelfApprovalBlocked:
    async def test_self_accept_refused(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project, self_approval="blocked"))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "self-edit",
            },
            project_path=project,
        )
        with pytest.raises(ProposalError, match="self-approval blocked"):
            await handle_resolve_proposal(
                tickets=tickets,
                threads=threads,
                sender="alice",
                args={
                    "proposal_id": propose["comment_id"],
                    "verdict": "accept",
                    "reasoning": "doesn't matter",
                },
                project_path=project,
            )


# ---- validation on accept --------------------------------------------------


class TestAcceptValidation:
    async def test_accept_on_whole_spec_rejects_unknown_field(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        """A ticket://spec change payload with a bogus field must fail at
        save time even though it routed successfully."""
        from jig.specs import SpecValidationError

        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec",
                "change": yaml.safe_dump({"not_a_field": "bloop"}),
            },
            project_path=project,
        )
        with pytest.raises(SpecValidationError):
            await handle_resolve_proposal(
                tickets=tickets,
                threads=threads,
                sender="pam",
                args={
                    "proposal_id": propose["comment_id"],
                    "verdict": "accept",
                },
                project_path=project,
            )

    async def test_cannot_accept_twice(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "v2",
            },
            project_path=project,
        )
        await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="pam",
            args={
                "proposal_id": propose["comment_id"],
                "verdict": "accept",
            },
            project_path=project,
        )
        with pytest.raises(ProposalError, match="not pending"):
            await handle_resolve_proposal(
                tickets=tickets,
                threads=threads,
                sender="pam",
                args={
                    "proposal_id": propose["comment_id"],
                    "verdict": "accept",
                },
                project_path=project,
            )


# ---- list_proposals -------------------------------------------------------


class TestEndToEnd:
    async def test_full_lifecycle_bumps_spec_version(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        """End-to-end Phase 3: ticket → spec → propose → accept → bump.

        Mirrors the plan's exit criteria for Task H. Different author
        accepts so the self-certification guard doesn't kick in.
        """
        # 1. Ticket exists.
        await _make_ticket(tickets)

        # 2. Initial spec written straight through save_ticket_spec.
        await _make_spec(project)
        spec0 = load_ticket_spec(project, "t-1")
        assert spec0 is not None
        assert spec0.version == 1
        assert spec0.fields["summary"] == "widget"

        # 3. Config with staffed PO + SA.
        save_config(project, _cfg_with_roles(project))

        # 4. Alice proposes a change to spec.summary.
        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "shiny new widget",
            },
            project_path=project,
        )
        assert propose["state"] == "pending"
        # Routed to PO (summary is po-owned per feature.yaml).
        assert propose["routing"]["role"] == "po"
        assert propose["routing"]["assignee"] == "pam"

        # 5. Pam (distinct actor) accepts.
        accept = await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="pam",
            args={
                "proposal_id": propose["comment_id"],
                "verdict": "accept",
            },
            project_path=project,
        )
        assert accept["state"] == "accepted"
        assert accept["self_approved"] is False
        assert accept["spec_version"] == 2

        # 6. Spec reloads at v2 with the new value.
        spec1 = load_ticket_spec(project, "t-1")
        assert spec1 is not None
        assert spec1.version == 2
        assert spec1.fields["summary"] == "shiny new widget"
        # Other fields untouched.
        assert spec1.fields["acceptance_criteria"] == ["B1 verified"]

        # 7. Re-accepting the same proposal fails loud.
        with pytest.raises(ProposalError, match="not pending"):
            await handle_resolve_proposal(
                tickets=tickets,
                threads=threads,
                sender="pam",
                args={
                    "proposal_id": propose["comment_id"],
                    "verdict": "accept",
                },
                project_path=project,
            )

        # 8. Listing pending proposals returns nothing; resolver entry
        #    carries state=accepted so the accepted filter surfaces it.
        pending = await handle_list_proposals(
            threads=threads,
            args={"ticket_id": "t-1", "state": "pending"},
        )
        accepted = await handle_list_proposals(
            threads=threads,
            args={"ticket_id": "t-1", "state": "accepted"},
        )
        assert pending == []
        assert len(accepted) >= 1


async def _post_accepted_handoff(
    threads: ThreadStore, ticket_id: str, phase: str
) -> None:
    await threads.post(
        Handoff(
            ticket_id=ticket_id,
            author="dev",
            phase=phase,
            summary=f"{phase} complete",
            acceptance_state="accepted",
            accepted_by="reviewer",
        )
    )


class TestSectionLocks:
    """Phase 5 Task M — ``section_locks`` from the work-type schema
    refuses proposal-accept once the locking phase has handed off."""

    async def test_accept_on_locked_section_after_handoff_refused(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        """``behaviors`` is locked after the ``spec`` phase on
        feature.yaml. Once an accepted Handoff for ``spec`` lands, a
        proposal accept targeting ``ticket://spec.behaviors`` must
        fail loud — even from a distinct reviewer."""
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.behaviors",
                "change": yaml.safe_dump([{"id": "B2", "when": "hover", "then": "ok"}]),
            },
            project_path=project,
        )

        # Spec phase just closed.
        await _post_accepted_handoff(threads, "t-1", "spec")

        with pytest.raises(ProposalError, match="locked"):
            await handle_resolve_proposal(
                tickets=tickets,
                threads=threads,
                sender="pam",
                args={
                    "proposal_id": propose["comment_id"],
                    "verdict": "accept",
                },
                project_path=project,
            )

        # Spec stays at v1 — accept was refused.
        spec = load_ticket_spec(project, "t-1")
        assert spec is not None and spec.version == 1

    async def test_accept_on_locked_section_before_handoff_allowed(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        """Same target, same config — but no accepted handoff yet.
        Accept must succeed."""
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.behaviors",
                "change": yaml.safe_dump([{"id": "B2", "when": "hover", "then": "ok"}]),
            },
            project_path=project,
        )
        result = await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="pam",
            args={
                "proposal_id": propose["comment_id"],
                "verdict": "accept",
            },
            project_path=project,
        )
        assert result["state"] == "accepted"
        assert result["spec_version"] == 2

    async def test_accept_on_unlocked_section_after_handoff_allowed(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        """``summary`` is not locked — editing it after a ``spec``
        handoff is still fine."""
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))
        await _post_accepted_handoff(threads, "t-1", "spec")

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "rewritten",
            },
            project_path=project,
        )
        result = await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="pam",
            args={
                "proposal_id": propose["comment_id"],
                "verdict": "accept",
            },
            project_path=project,
        )
        assert result["state"] == "accepted"
        assert result["spec_version"] == 2

    async def test_whole_spec_accept_touching_locked_field_refused(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        """``ticket://spec`` accepts must inspect every field in the
        payload and refuse if any intersect the lock map."""
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))
        await _post_accepted_handoff(threads, "t-1", "spec")

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec",
                "change": yaml.safe_dump(
                    {
                        "summary": "fine",
                        "behaviors": [{"id": "B9", "when": "resize", "then": "ok"}],
                    }
                ),
            },
            project_path=project,
        )
        with pytest.raises(ProposalError, match="locked"):
            await handle_resolve_proposal(
                tickets=tickets,
                threads=threads,
                sender="pam",
                args={
                    "proposal_id": propose["comment_id"],
                    "verdict": "accept",
                },
                project_path=project,
            )

    async def test_pending_handoff_does_not_lock(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        """Only **accepted** handoffs trip the lock; a pending one
        leaves the section editable."""
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))

        # Pending — evaluator hasn't signed off yet.
        await threads.post(
            Handoff(
                ticket_id="t-1",
                author="dev",
                phase="spec",
                summary="spec in review",
            )
        )

        propose = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.behaviors",
                "change": yaml.safe_dump([{"id": "B2", "when": "hover", "then": "ok"}]),
            },
            project_path=project,
        )
        result = await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="pam",
            args={
                "proposal_id": propose["comment_id"],
                "verdict": "accept",
            },
            project_path=project,
        )
        assert result["state"] == "accepted"


class TestListProposals:
    async def test_filter_by_ticket_and_state(
        self,
        project: Path,
        tickets: TicketStore,
        threads: ThreadStore,
    ) -> None:
        await _make_ticket(tickets)
        await _make_spec(project)
        save_config(project, _cfg_with_roles(project))

        a = await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "x",
            },
            project_path=project,
        )
        await handle_propose_change(
            tickets=tickets,
            threads=threads,
            sender="alice",
            args={
                "ticket_id": "t-1",
                "target": "ticket://spec.summary",
                "change": "y",
            },
            project_path=project,
        )
        await handle_resolve_proposal(
            tickets=tickets,
            threads=threads,
            sender="pam",
            args={"proposal_id": a["comment_id"], "verdict": "reject"},
            project_path=project,
        )

        pending = await handle_list_proposals(
            threads=threads,
            args={"ticket_id": "t-1", "state": "pending"},
        )
        rejected = await handle_list_proposals(
            threads=threads,
            args={"ticket_id": "t-1", "state": "rejected"},
        )
        all_for_ticket = await handle_list_proposals(
            threads=threads,
            args={"ticket_id": "t-1"},
        )
        assert len(pending) == 1
        assert len(rejected) >= 1  # resolver entry carries state=rejected
        # all_for_ticket skips resolver entries by default.
        assert all(p["parent_id"] is None for p in all_for_ticket)
