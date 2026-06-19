"""Tests for the VD MCP tool handlers + role config (Track D MVP)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.intent import ComplicationsConsidered, Intent
from jig.persistence import load_role
from jig.schemas.design_system import (
    Component,
    ComponentVariant,
    DesignToken,
)
from jig.schemas.frontend import FrontendSpec
from jig.spec_loader import (
    brand_path,
    components_path,
    frontend_spec_path,
    load_brand,
    load_components,
    load_tokens,
    tokens_path,
    wireframe_notes_path,
    wireframe_path,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff
from jig.ticket import Ticket, TicketStatus, WorkType
from jig.vd_mcp import (
    VD_NEXT_PHASE,
    VD_TICKET_ID,
    handle_vd_finalize,
    handle_vd_import_claude_design,
    handle_vd_set_component,
    handle_vd_set_design_token,
    handle_vd_set_wireframe,
    handle_wireframe_get_notes,
    handle_wireframe_lint,
    handle_wireframe_set_notes,
)
from jig.wireframes.format import WireframeMeta, render_meta_comment


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _intent() -> Intent:
    return Intent(
        problem="Project needs a declared frontend stack so dev agents know what to author against.",
        simplest_solution="Take the default minimal stack.",
        complications_considered=ComplicationsConsidered(),
    )


def _meta(screen_id: str = "post-a-job", title: str = "Post a job") -> WireframeMeta:
    return WireframeMeta(
        screen_id=screen_id,
        title=title,
        persona_targets=["merchant"],
        journey_refs=["j-merchant-onboarding"],
    )


def _clean_wireframe_html(meta: WireframeMeta) -> str:
    return f"""\
{render_meta_comment(meta)}
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{meta.title}</title>
<link rel="stylesheet" href="../wireframe.css">
<script src="https://cdn.jsdelivr.net/npm/alpinejs@3" defer></script>
</head><body class="min-h-screen">
<main class="container mx-auto p-6"><h1>{meta.title}</h1></main>
</body></html>
"""


@pytest.fixture
async def stores(tmp_path: Path):
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    bus = MessageBus(store_dir / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    yield tickets, threads, bus


# ---------------------------------------------------------------------------
# vd_set_wireframe
# ---------------------------------------------------------------------------


class TestSetWireframe:
    async def test_writes_clean_wireframe(self, tmp_path: Path) -> None:
        meta = _meta()
        html = _clean_wireframe_html(meta)
        result = await handle_vd_set_wireframe(
            project_path=tmp_path,
            screen_id="post-a-job",
            html=html,
        )
        assert result["screen_id"] == "post-a-job"
        assert wireframe_path(tmp_path, "post-a-job").is_file()

    async def test_critical_lint_failure_raises(self, tmp_path: Path) -> None:
        # Inline style is critical — should block the upsert.
        bad = (
            f"{render_meta_comment(_meta())}\n"
            '<html><body><div style="color:red"></div></body></html>'
        )
        with pytest.raises(ValueError, match="critical lint error"):
            await handle_vd_set_wireframe(
                project_path=tmp_path,
                screen_id="post-a-job",
                html=bad,
            )
        # File must NOT have been written.
        assert not wireframe_path(tmp_path, "post-a-job").is_file()

    async def test_meta_arg_splices_in_fresh_meta(self, tmp_path: Path) -> None:
        meta = _meta(title="Post a job v2")
        # HTML carries a stale meta comment; the meta arg should replace it.
        stale_meta = render_meta_comment(_meta(title="OLD TITLE"))
        html = f"{stale_meta}\n<!doctype html><html><body></body></html>"
        await handle_vd_set_wireframe(
            project_path=tmp_path,
            screen_id="post-a-job",
            html=html,
            meta=meta.model_dump(mode="json"),
        )
        on_disk = wireframe_path(tmp_path, "post-a-job").read_text()
        assert "Post a job v2" in on_disk
        assert "OLD TITLE" not in on_disk


# ---------------------------------------------------------------------------
# wireframe_lint
# ---------------------------------------------------------------------------


class TestWireframeLintTool:
    async def test_returns_violations_as_dicts(self) -> None:
        violations = await handle_wireframe_lint(html="<html></html>")
        assert isinstance(violations, list)
        # No meta block — should produce at least the meta-missing violation.
        codes = {v["code"] for v in violations}
        assert "meta-missing" in codes


# ---------------------------------------------------------------------------
# wireframe_get/set_notes
# ---------------------------------------------------------------------------


class TestWireframeNotes:
    async def test_get_returns_empty_when_absent(self, tmp_path: Path) -> None:
        text = await handle_wireframe_get_notes(
            project_path=tmp_path, screen_id="signup"
        )
        assert text == ""

    async def test_set_then_get_round_trip(self, tmp_path: Path) -> None:
        notes = "## States\n- default\n- error\n"
        path_str = await handle_wireframe_set_notes(
            project_path=tmp_path, screen_id="signup", notes=notes
        )
        assert path_str.endswith("signup.notes.md")
        text = await handle_wireframe_get_notes(
            project_path=tmp_path, screen_id="signup"
        )
        assert text == notes
        assert wireframe_notes_path(tmp_path, "signup").is_file()


# ---------------------------------------------------------------------------
# vd_set_design_token / vd_set_component
# ---------------------------------------------------------------------------


class TestSetDesignToken:
    async def test_upserts_into_tokens_yaml(self, tmp_path: Path) -> None:
        token = DesignToken(id="color-primary", kind="color", value="#0a66c2")
        tid = await handle_vd_set_design_token(
            project_path=tmp_path, token=token.model_dump(mode="json")
        )
        assert tid == "color-primary"
        loaded = load_tokens(tmp_path)
        # Source flips to operator_supplied on first operator touch.
        assert loaded.source == "operator_supplied"
        ids = {t.id for t in loaded.tokens}
        assert "color-primary" in ids

    async def test_upsert_replaces_existing(self, tmp_path: Path) -> None:
        await handle_vd_set_design_token(
            project_path=tmp_path,
            token={"id": "color-primary", "kind": "color", "value": "#000"},
        )
        await handle_vd_set_design_token(
            project_path=tmp_path,
            token={"id": "color-primary", "kind": "color", "value": "#fff"},
        )
        loaded = load_tokens(tmp_path)
        # Only one entry for color-primary — the second.
        primaries = [t for t in loaded.tokens if t.id == "color-primary"]
        assert len(primaries) == 1
        assert primaries[0].value == "#fff"


class TestSetComponent:
    async def test_upserts_into_components_yaml(self, tmp_path: Path) -> None:
        comp = Component(
            id="modal",
            name="Modal",
            variants=[ComponentVariant(id="default")],
        )
        cid = await handle_vd_set_component(
            project_path=tmp_path, component=comp.model_dump(mode="json")
        )
        assert cid == "modal"
        loaded = load_components(tmp_path)
        assert loaded.source == "operator_supplied"
        ids = {c.id for c in loaded.components}
        assert "modal" in ids


# ---------------------------------------------------------------------------
# vd_finalize
# ---------------------------------------------------------------------------


class TestVdFinalize:
    async def test_writes_artifacts_and_resolves_ticket(
        self, tmp_path: Path, stores
    ) -> None:
        tickets, threads, bus = stores
        await tickets.create(
            Ticket(
                id=VD_TICKET_ID,
                work_type=WorkType.BRIEF,
                title="VD — frontend",
                created_by="test",
            )
        )

        spec = FrontendSpec(intent=_intent(), allowed_dependencies=["alpinejs"])
        meta = _meta()
        wf_html = _clean_wireframe_html(meta)
        entry_id = await handle_vd_finalize(
            tickets=tickets,
            threads=threads,
            bus=bus,
            project_path=tmp_path,
            frontend=spec.model_dump(mode="json"),
            wireframes=[
                {
                    "screen_id": "post-a-job",
                    "html": wf_html,
                }
            ],
            summary="VD finalized: 1 wireframe + default stack",
            author="vd",
        )
        assert entry_id

        # Artifacts on disk
        assert frontend_spec_path(tmp_path).is_file()
        assert wireframe_path(tmp_path, "post-a-job").is_file()

        # Ticket resolved
        ticket = await tickets.get(VD_TICKET_ID)
        assert ticket is not None
        assert ticket.status == TicketStatus.RESOLVED

        # Handoff posted, targeting PM
        entries = await threads.for_ticket(VD_TICKET_ID)
        handoffs = [e for e in entries if isinstance(e, Handoff)]
        assert len(handoffs) >= 1
        assert handoffs[0].phase == VD_NEXT_PHASE

    async def test_critical_lint_blocks_finalize_atomically(
        self, tmp_path: Path, stores
    ) -> None:
        tickets, threads, bus = stores
        await tickets.create(
            Ticket(
                id=VD_TICKET_ID,
                work_type=WorkType.BRIEF,
                title="VD — frontend",
                created_by="test",
            )
        )

        spec = FrontendSpec(intent=_intent())
        meta = _meta()
        good_html = _clean_wireframe_html(meta)
        bad_html = (
            f"{render_meta_comment(_meta('signup', 'Sign up'))}\n"
            '<html><body><div style="color:red"></div></body></html>'
        )
        with pytest.raises(ValueError, match="critical lint"):
            await handle_vd_finalize(
                tickets=tickets,
                threads=threads,
                bus=bus,
                project_path=tmp_path,
                frontend=spec.model_dump(mode="json"),
                wireframes=[
                    {"screen_id": "post-a-job", "html": good_html},
                    {"screen_id": "signup", "html": bad_html},
                ],
                summary="should not land",
                author="vd",
            )
        # Atomicity: NEITHER wireframe written, frontend.yaml NOT written.
        assert not wireframe_path(tmp_path, "post-a-job").is_file()
        assert not wireframe_path(tmp_path, "signup").is_file()
        assert not frontend_spec_path(tmp_path).is_file()

    async def test_backend_only_project_finalizes_with_empty_wireframes(
        self, tmp_path: Path, stores
    ) -> None:
        tickets, threads, bus = stores
        await tickets.create(
            Ticket(
                id=VD_TICKET_ID,
                work_type=WorkType.BRIEF,
                title="VD — frontend",
                created_by="test",
            )
        )
        spec = FrontendSpec(intent=_intent())
        await handle_vd_finalize(
            tickets=tickets,
            threads=threads,
            bus=bus,
            project_path=tmp_path,
            frontend=spec.model_dump(mode="json"),
            wireframes=[],
            summary="backend-only project: VD has nothing to do",
            author="vd",
        )
        ticket = await tickets.get(VD_TICKET_ID)
        assert ticket is not None
        assert ticket.status == TicketStatus.RESOLVED
        assert frontend_spec_path(tmp_path).is_file()


# ---------------------------------------------------------------------------
# vd_import_claude_design
# ---------------------------------------------------------------------------


class TestImportClaudeDesign:
    async def test_writes_tokens_components_brand_wireframes(
        self, tmp_path: Path
    ) -> None:
        meta = _meta(screen_id="signup", title="Sign up")
        result = await handle_vd_import_claude_design(
            project_path=tmp_path,
            payload={
                "tokens": [
                    {"id": "color-primary", "kind": "color", "value": "#0a66c2"},
                ],
                "components": [
                    {
                        "id": "button",
                        "name": "Button",
                        "variants": [{"id": "primary"}],
                    }
                ],
                "brand": {"voice": "bold and direct", "tone": "punchy"},
                "wireframes": [
                    {
                        "screen_id": "signup",
                        "html": _clean_wireframe_html(meta),
                    }
                ],
            },
        )
        assert result["source"] == "claude_design"
        assert result["imported"]["tokens"] == 1
        assert result["imported"]["components"] == 1
        assert result["imported"]["brand"] == 1
        assert result["imported"]["wireframes"] == 1

        # Disk verification: source set to claude_design.
        assert tokens_path(tmp_path).is_file()
        loaded_tokens = load_tokens(tmp_path)
        assert loaded_tokens.source == "claude_design"

        assert components_path(tmp_path).is_file()
        loaded_components = load_components(tmp_path)
        assert loaded_components.source == "claude_design"

        assert brand_path(tmp_path).is_file()
        loaded_brand = load_brand(tmp_path)
        assert loaded_brand.source == "claude_design"
        assert loaded_brand.voice == "bold and direct"

        assert wireframe_path(tmp_path, "signup").is_file()

    async def test_partial_payload_only_writes_what_is_provided(
        self, tmp_path: Path
    ) -> None:
        result = await handle_vd_import_claude_design(
            project_path=tmp_path,
            payload={
                "tokens": [
                    {"id": "color-primary", "kind": "color", "value": "#000"},
                ],
            },
        )
        assert "tokens" in result["imported"]
        assert "components" not in result["imported"]
        # Components / brand stay absent on disk → loaders return defaults.
        assert not components_path(tmp_path).is_file()
        assert not brand_path(tmp_path).is_file()


# ---------------------------------------------------------------------------
# Role config + registration
# ---------------------------------------------------------------------------


class TestVdRoleConfig:
    def test_vd_role_yaml_loads(self, tmp_path: Path) -> None:
        # Loads from shipped defaults dir (no project override needed).
        cfg = load_role(tmp_path, "vd")
        assert cfg.role == "vd"
        assert cfg.strict_tools is True
        # The MVP allowlist must include the load-bearing tools.
        for tool_name in (
            "vd_finalize",
            "vd_set_wireframe",
            "vd_set_design_token",
            "vd_set_component",
            "wireframe_lint",
            "wireframe_get_notes",
            "wireframe_set_notes",
            "vd_import_claude_design",
            "Read",
            "ask_question",
        ):
            assert tool_name in cfg.allowed_tools, f"missing: {tool_name}"

    def test_vd_role_yaml_is_well_formed(self) -> None:
        # Lint via direct YAML load — guards against accidental
        # malformed yaml landing in the shipped defaults.
        from jig.persistence import _defaults_dir

        path = _defaults_dir() / "roles" / "vd.yaml"
        assert path.is_file()
        data = yaml.safe_load(path.read_text())
        assert data["role"] == "vd"
