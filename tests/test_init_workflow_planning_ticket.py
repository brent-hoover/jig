"""Regression tests for ``_create_planning_ticket`` — specifically the
scaffold-summary section that flows architecture facts into the PM's
ticket context so it doesn't plan a 'project setup' / 'skeleton' ticket
duplicating what the template already shipped.

The PM role has no file-read tools by design
(:doc:`feedback_pm_no_file_tools`), so the template inventory has to
arrive in the planning ticket's description, not be discoverable on
disk."""

from __future__ import annotations

from pathlib import Path

import yaml

from jig.init_workflow import _create_planning_ticket, _scaffold_summary_for_pm
from jig.store.tickets import TicketStore


def _seed_architecture(
    project_path: Path,
    *,
    template: str = "python-cli",
    language: str = "python",
    framework: str | None = "typer",
    decisions: dict[str, object] | None = None,
) -> None:
    arch = project_path / ".jig" / "spec" / "architecture.yaml"
    arch.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {"template": template, "language": language}
    if framework is not None:
        data["framework"] = framework
    if decisions is not None:
        data["decisions"] = decisions
    arch.write_text(yaml.safe_dump(data, sort_keys=False))


def test_scaffold_summary_lists_template_and_blocks_setup_tickets(
    tmp_path: Path,
) -> None:
    """When a template has been applied, the PM-facing summary names it
    explicitly and tells the PM not to duplicate scaffolding work."""
    _seed_architecture(
        tmp_path,
        template="python-cli",
        language="python",
        framework="typer",
        decisions={"cli_framework": "typer", "http_client": "httpx"},
    )
    summary = _scaffold_summary_for_pm(tmp_path)
    assert summary != ""
    assert "python-cli" in summary
    assert "typer" in summary
    assert "httpx" in summary
    # The actionable instruction the PM needs.
    assert (
        "do NOT plan tickets" in summary.lower()
        or "do not plan tickets" in summary.lower()
    )
    assert "skeleton" in summary.lower()


def test_scaffold_summary_empty_when_no_architecture_yaml(
    tmp_path: Path,
) -> None:
    """Missing or malformed architecture.yaml is graceful — caller
    appends an empty string unconditionally."""
    assert _scaffold_summary_for_pm(tmp_path) == ""


def test_scaffold_summary_empty_when_template_field_missing(
    tmp_path: Path,
) -> None:
    """architecture.yaml without a ``template`` key isn't actionable."""
    arch = tmp_path / ".jig" / "spec" / "architecture.yaml"
    arch.parent.mkdir(parents=True, exist_ok=True)
    arch.write_text(yaml.safe_dump({"language": "python"}))
    assert _scaffold_summary_for_pm(tmp_path) == ""


def test_scaffold_summary_empty_when_yaml_malformed(
    tmp_path: Path,
) -> None:
    """A corrupt architecture.yaml does not crash init."""
    arch = tmp_path / ".jig" / "spec" / "architecture.yaml"
    arch.parent.mkdir(parents=True, exist_ok=True)
    arch.write_text("this: is: not: valid: yaml: [")
    assert _scaffold_summary_for_pm(tmp_path) == ""


def test_scaffold_summary_empty_when_yaml_is_not_mapping(
    tmp_path: Path,
) -> None:
    """``yaml.safe_load`` on a top-level list / string / scalar returns
    a non-mapping. Without an ``isinstance(data, dict)`` guard, the
    subsequent ``.get()`` would raise ``AttributeError`` and crash
    init. Treat same as missing — return empty string."""
    arch = tmp_path / ".jig" / "spec" / "architecture.yaml"
    arch.parent.mkdir(parents=True, exist_ok=True)
    # Top-level list — syntactically valid YAML, wrong shape for an
    # architecture file.
    arch.write_text("- not-an-architecture-mapping\n- just-a-list\n")
    assert _scaffold_summary_for_pm(tmp_path) == ""
    # Top-level scalar string — also valid YAML, also wrong shape.
    arch.write_text("just-a-string\n")
    assert _scaffold_summary_for_pm(tmp_path) == ""


async def test_create_planning_ticket_embeds_scaffold_summary(
    tmp_path: Path,
) -> None:
    """End-to-end: the planning ticket's description includes the
    scaffold-already-done section so PM consumes it via
    ``ticket://description`` context resolution."""
    _seed_architecture(tmp_path, template="python-cli")
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    await _create_planning_ticket(tickets, tmp_path)
    planning = await tickets.get("planning")
    assert planning is not None
    assert "python-cli" in planning.description
    assert "Already scaffolded" in planning.description


async def test_create_planning_ticket_omits_scaffold_when_no_architecture(
    tmp_path: Path,
) -> None:
    """If architecture.yaml is missing (e.g. non-init code path),
    the planning ticket still creates cleanly — description just lacks
    the scaffold section. Spec reference still present."""
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    await _create_planning_ticket(tickets, tmp_path)
    planning = await tickets.get("planning")
    assert planning is not None
    assert "Break down the project spec" in planning.description
    assert "Already scaffolded" not in planning.description


async def test_create_planning_ticket_idempotent(tmp_path: Path) -> None:
    """Re-invocation does not overwrite an existing planning ticket."""
    _seed_architecture(tmp_path, template="python-cli")
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    await _create_planning_ticket(tickets, tmp_path)
    first = await tickets.get("planning")
    assert first is not None
    # Mutate the architecture file to confirm the second call wouldn't
    # rewrite the ticket from new data.
    _seed_architecture(tmp_path, template="fastapi", framework="fastapi")
    await _create_planning_ticket(tickets, tmp_path)
    second = await tickets.get("planning")
    assert second is not None
    assert second.description == first.description
