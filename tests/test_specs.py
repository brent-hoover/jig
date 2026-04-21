"""Tests for jig.specs — ticket spec model + storage (Phase 3 Task C).

Covers: write-time schema validation, version bumping, unknown-field
rejection, load/delete/list, and snapshot semantics for work_type + size.
"""

from pathlib import Path

import pytest
import yaml

from jig.persistence import init_project
from jig.specs import (
    SpecValidationError,
    TicketSpec,
    delete_ticket_spec,
    list_ticket_specs,
    load_ticket_spec,
    save_ticket_spec,
)
from jig.ticket import Size, WorkType


@pytest.fixture
def initialized_project(tmp_path: Path) -> Path:
    """Mimics `jig init` so .jig/specs/ exists."""
    (tmp_path / ".git").mkdir()
    init_project(tmp_path)
    return tmp_path


def _feature_spec(
    ticket_id: str = "t-1",
    size: Size = Size.M,
    **fields_overrides: object,
) -> TicketSpec:
    fields: dict = {
        "summary": "short",
        "behaviors": [{"id": "B1", "when": "click", "then": "save"}],
        "acceptance_criteria": ["B1 verified"],
        "out_of_scope": ["collab editing"],
    }
    fields.update(fields_overrides)
    return TicketSpec(
        ticket_id=ticket_id,
        work_type=WorkType.FEATURE,
        size=size,
        fields=fields,
    )


class TestRoundTrip:
    def test_save_load(self, initialized_project: Path) -> None:
        spec = _feature_spec()
        save_ticket_spec(initialized_project, spec)
        loaded = load_ticket_spec(initialized_project, "t-1")
        assert loaded is not None
        assert loaded.work_type == WorkType.FEATURE
        assert loaded.size == Size.M
        assert loaded.fields["summary"] == "short"

    def test_load_missing_returns_none(self, initialized_project: Path) -> None:
        assert load_ticket_spec(initialized_project, "never") is None

    def test_list_empty(self, initialized_project: Path) -> None:
        assert list_ticket_specs(initialized_project) == []

    def test_list_sorted(self, initialized_project: Path) -> None:
        save_ticket_spec(initialized_project, _feature_spec("zzz"))
        save_ticket_spec(initialized_project, _feature_spec("aaa"))
        save_ticket_spec(initialized_project, _feature_spec("mmm"))
        assert list_ticket_specs(initialized_project) == ["aaa", "mmm", "zzz"]


class TestVersionBump:
    def test_first_write_is_version_one(self, initialized_project: Path) -> None:
        saved = save_ticket_spec(initialized_project, _feature_spec())
        assert saved.version == 1

    def test_second_write_bumps(self, initialized_project: Path) -> None:
        save_ticket_spec(initialized_project, _feature_spec())
        saved = save_ticket_spec(
            initialized_project,
            _feature_spec(summary="rewritten"),
        )
        assert saved.version == 2
        on_disk = load_ticket_spec(initialized_project, "t-1")
        assert on_disk is not None
        assert on_disk.version == 2
        assert on_disk.fields["summary"] == "rewritten"

    def test_bump_version_false_preserves(
        self, initialized_project: Path
    ) -> None:
        save_ticket_spec(initialized_project, _feature_spec())
        explicit = _feature_spec(summary="explicit")
        # mimic a proposal-accept path that computed version itself.
        explicit = explicit.model_copy(update={"version": 9})
        saved = save_ticket_spec(
            initialized_project, explicit, bump_version=False
        )
        assert saved.version == 9


class TestSchemaValidation:
    def test_missing_required_m_rejected(
        self, initialized_project: Path
    ) -> None:
        """Feature at M requires summary+behaviors+AC+oos."""
        bad = TicketSpec(
            ticket_id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            fields={"summary": "only"},
        )
        with pytest.raises(SpecValidationError, match="missing required fields"):
            save_ticket_spec(initialized_project, bad)

    def test_xs_requires_only_summary(
        self, initialized_project: Path
    ) -> None:
        xs = TicketSpec(
            ticket_id="t-xs",
            work_type=WorkType.FEATURE,
            size=Size.XS,
            fields={"summary": "tiny"},
        )
        save_ticket_spec(initialized_project, xs)  # no raise

    def test_unknown_field_rejected(
        self, initialized_project: Path
    ) -> None:
        with pytest.raises(SpecValidationError, match="unknown fields"):
            save_ticket_spec(
                initialized_project,
                _feature_spec(note_from_space="bloop"),
            )

    def test_empty_string_counts_as_missing(
        self, initialized_project: Path
    ) -> None:
        bad = _feature_spec(summary="")
        with pytest.raises(SpecValidationError, match="summary"):
            save_ticket_spec(initialized_project, bad)

    def test_empty_list_counts_as_missing(
        self, initialized_project: Path
    ) -> None:
        bad = _feature_spec(behaviors=[])
        with pytest.raises(SpecValidationError, match="behaviors"):
            save_ticket_spec(initialized_project, bad)

    def test_spike_without_behaviors_accepted(
        self, initialized_project: Path
    ) -> None:
        """Spike has no behaviors in the schema, so omitting it is fine."""
        spec = TicketSpec(
            ticket_id="spike-1",
            work_type=WorkType.SPIKE,
            size=Size.M,
            fields={
                "summary": "hot take",
                "question": "why slow?",
                "methods": ["benchmark"],
                "time_box": "2d",
                "exit_criteria": ["hypothesis answered"],
            },
        )
        save_ticket_spec(initialized_project, spec)


class TestDelete:
    def test_delete_existing(self, initialized_project: Path) -> None:
        save_ticket_spec(initialized_project, _feature_spec())
        assert delete_ticket_spec(initialized_project, "t-1") is True
        assert load_ticket_spec(initialized_project, "t-1") is None

    def test_delete_missing(self, initialized_project: Path) -> None:
        assert delete_ticket_spec(initialized_project, "never") is False


class TestInitCreatesSpecsDir:
    def test_init_creates_specs_dir(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        init_project(tmp_path)
        assert (tmp_path / ".jig" / "specs").is_dir()


class TestYAMLShapeOnDisk:
    def test_field_values_preserve_types(
        self, initialized_project: Path
    ) -> None:
        """Complex structured fields must roundtrip untouched."""
        spec = _feature_spec(
            behaviors=[
                {"id": "B1", "when": "click", "then": ["save", "notify"]},
                {"id": "B2", "when": "refresh", "then": "reload"},
            ],
        )
        save_ticket_spec(initialized_project, spec)
        raw = yaml.safe_load(
            (initialized_project / ".jig" / "specs" / "t-1.yaml").read_text()
        )
        assert raw["fields"]["behaviors"][0]["id"] == "B1"
        assert raw["fields"]["behaviors"][0]["then"] == ["save", "notify"]
