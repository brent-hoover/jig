"""IssueService — the context-free CRUD/link/approve seam over the stores.

Every front-door create lands as PROPOSED with a jig-N key, enforces the AC +
work_type contract, and is reachable by key or UUID. PROPOSED -> OPEN is only
via approve(); the generic update path rejects it.
"""

from pathlib import Path

import pytest

from jig.issues.service import IssueService
from jig.ticket import TicketStatus
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _service(tmp_path: Path) -> IssueService:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    return IssueService(tmp_path)


@pytest.mark.asyncio
async def test_create_lands_proposed_with_key(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    t = await svc.create(
        title="add export",
        work_type="feature",
        description=TICKET_AC_PLACEHOLDER,
        created_by="agent-x",
    )
    assert t.status is TicketStatus.PROPOSED
    assert t.key == "jig-1"
    assert t.created_by == "agent-x"


@pytest.mark.asyncio
async def test_create_without_ac_fails_loudly_and_writes_nothing(
    tmp_path: Path,
) -> None:
    svc = _service(tmp_path)
    with pytest.raises(ValueError, match="(?i)acceptance"):
        await svc.create(
            title="vague", work_type="feature", description="no criteria here"
        )
    assert await svc.list() == []


@pytest.mark.asyncio
async def test_create_invalid_work_type_fails(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with pytest.raises(ValueError, match="work_type"):
        await svc.create(
            title="x", work_type="nonsense", description=TICKET_AC_PLACEHOLDER
        )


@pytest.mark.asyncio
async def test_get_resolves_by_key_or_uuid(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    t = await svc.create(
        title="x", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    by_key = await svc.get(t.key)
    by_uuid = await svc.get(t.id)
    assert by_key.id == t.id and by_uuid.id == t.id


@pytest.mark.asyncio
async def test_approve_promotes_but_update_cannot(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    t = await svc.create(
        title="x", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )

    with pytest.raises(ValueError, match="approv"):
        await svc.update(t.key, status="open")

    promoted = await svc.approve(t.key)
    assert promoted.status is TicketStatus.OPEN


@pytest.mark.asyncio
async def test_link_sets_block_and_reverse_edge(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = await svc.create(
        title="a", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    b = await svc.create(
        title="b", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )

    await svc.link(a.key, blocked_by=[b.key])

    assert b.id in (await svc.get(a.key)).blocked_by
    assert a.id in (await svc.get(b.key)).blocks


@pytest.mark.asyncio
async def test_comment_posts_note(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    t = await svc.create(
        title="x", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    await svc.comment(t.key, "looks reasonable", author="agent-x")

    notes = await svc.comments(t.key)
    assert any("looks reasonable" in getattr(n, "text", "") for n in notes)


@pytest.mark.asyncio
async def test_list_filters_by_status(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    keep = await svc.create(
        title="keep", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    other = await svc.create(
        title="other", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    await svc.approve(other.key)  # now OPEN

    proposed = await svc.list(status="proposed")
    assert [t.id for t in proposed] == [keep.id]


@pytest.mark.asyncio
async def test_create_with_bad_dependency_writes_nothing(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with pytest.raises(KeyError):
        await svc.create(
            title="x",
            work_type="feature",
            description=TICKET_AC_PLACEHOLDER,
            blocked_by=["jig-999"],
        )
    assert await svc.list() == []


@pytest.mark.asyncio
async def test_link_is_atomic_on_bad_ref(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = await svc.create(
        title="a", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    b = await svc.create(
        title="b", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )

    # A good ref followed by a bad one must apply NEITHER edge.
    with pytest.raises(KeyError):
        await svc.link(a.key, blocked_by=[b.key, "jig-999"])

    assert (await svc.get(a.key)).blocked_by == []
    assert (await svc.get(b.key)).blocks == []


@pytest.mark.asyncio
async def test_update_rejects_reserved_field(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    t = await svc.create(
        title="x", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    with pytest.raises(ValueError, match="reserved"):
        await svc.update(t.key, _allow_approval=True, status="open")


@pytest.mark.asyncio
async def test_approve_rejects_non_proposed(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    t = await svc.create(
        title="x", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    await svc.approve(t.key)
    with pytest.raises(ValueError, match="(?i)proposed"):
        await svc.approve(t.key)


@pytest.mark.asyncio
async def test_link_remove_parent_validates_ref(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = await svc.create(
        title="a", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    b = await svc.create(
        title="b", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    await svc.link(a.key, parent=b.key)

    # A bad parent ref must raise even when removing — not silently clear.
    with pytest.raises(KeyError):
        await svc.link(a.key, parent="jig-999", remove=True)
    assert (await svc.get(a.key)).parent_id == b.id


@pytest.mark.asyncio
async def test_link_remove_parent_only_clears_matching(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    a = await svc.create(
        title="a", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    b = await svc.create(
        title="b", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    c = await svc.create(
        title="c", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    await svc.link(a.key, parent=b.key)

    # Removing a non-matching parent is a no-op; removing the real one clears it.
    await svc.link(a.key, parent=c.key, remove=True)
    assert (await svc.get(a.key)).parent_id == b.id
    await svc.link(a.key, parent=b.key, remove=True)
    assert (await svc.get(a.key)).parent_id is None
