"""Tests for jig.specs — ticket spec model + storage (Phase 3 Task C).

Covers: write-time schema validation, version bumping, unknown-field
rejection, load/delete/list, and snapshot semantics for work_type + size.
"""

from pathlib import Path

import pytest
import yaml

from datetime import datetime, timezone

from jig.persistence import init_project
from jig.spec_schema import Behavior, Capability, CapabilityState
from jig.specs import (
    SpecValidationError,
    TicketSpec,
    delete_ticket_spec,
    list_ticket_specs,
    load_ticket_spec,
    materialize_ticket_spec_from_capability,
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

    def test_bump_version_false_preserves(self, initialized_project: Path) -> None:
        save_ticket_spec(initialized_project, _feature_spec())
        explicit = _feature_spec(summary="explicit")
        # mimic a proposal-accept path that computed version itself.
        explicit = explicit.model_copy(update={"version": 9})
        saved = save_ticket_spec(initialized_project, explicit, bump_version=False)
        assert saved.version == 9


class TestSchemaValidation:
    def test_missing_required_m_rejected(self, initialized_project: Path) -> None:
        """Feature at M requires summary+behaviors+AC+oos."""
        bad = TicketSpec(
            ticket_id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            fields={"summary": "only"},
        )
        with pytest.raises(SpecValidationError, match="missing required fields"):
            save_ticket_spec(initialized_project, bad)

    def test_xs_requires_only_summary(self, initialized_project: Path) -> None:
        xs = TicketSpec(
            ticket_id="t-xs",
            work_type=WorkType.FEATURE,
            size=Size.XS,
            fields={"summary": "tiny"},
        )
        save_ticket_spec(initialized_project, xs)  # no raise

    def test_unknown_field_rejected(self, initialized_project: Path) -> None:
        with pytest.raises(SpecValidationError, match="unknown fields"):
            save_ticket_spec(
                initialized_project,
                _feature_spec(note_from_space="bloop"),
            )

    def test_empty_string_counts_as_missing(self, initialized_project: Path) -> None:
        bad = _feature_spec(summary="")
        with pytest.raises(SpecValidationError, match="summary"):
            save_ticket_spec(initialized_project, bad)

    def test_empty_list_counts_as_missing(self, initialized_project: Path) -> None:
        bad = _feature_spec(behaviors=[])
        with pytest.raises(SpecValidationError, match="behaviors"):
            save_ticket_spec(initialized_project, bad)

    def test_spike_without_behaviors_accepted(self, initialized_project: Path) -> None:
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
    def test_field_values_preserve_types(self, initialized_project: Path) -> None:
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


def _capability(
    cap_id: str = "fetch-top-stories",
    *,
    behaviors: list[Behavior] | None = None,
    acceptance_criteria: list[str] | None = None,
    excluded: list[str] | None = None,
    summary: str = "Fetch HN top stories",
) -> Capability:
    now = datetime.now(timezone.utc)
    return Capability(
        id=cap_id,
        title="Fetch top stories",
        state=CapabilityState.PLANNED,
        summary=summary,
        behaviors=behaviors
        if behaviors is not None
        else [
            Behavior(
                id="run-top",
                description="`hn-cli top --limit N` prints N stories.",
                acceptance_criteria=["Exit code is 0 on success."],
            )
        ],
        acceptance_criteria=acceptance_criteria or [],
        excluded=excluded if excluded is not None else [],
        created_at=now,
        last_updated=now,
        state_changed_at=now,
    )


class TestMaterializeFromCapability:
    def test_maps_all_four_fields(self) -> None:
        cap = _capability(
            summary="Pull stories",
            excluded=["pagination", "caching"],
            acceptance_criteria=["Top-level AC item"],
        )
        spec = materialize_ticket_spec_from_capability(
            ticket_id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            capability=cap,
        )
        assert spec.ticket_id == "t-1"
        assert spec.work_type == WorkType.FEATURE
        assert spec.size == Size.M
        assert spec.fields["summary"] == "Pull stories"
        assert spec.fields["acceptance_criteria"] == ["Top-level AC item"]
        assert spec.fields["out_of_scope"] == ["pagination", "caching"]
        assert len(spec.fields["behaviors"]) == 1

    def test_behaviors_serialise_as_plain_dicts(self) -> None:
        cap = _capability(
            behaviors=[
                Behavior(
                    id="b-one",
                    description="behaviour one",
                    acceptance_criteria=["AC one"],
                ),
                Behavior(
                    id="b-two",
                    description="behaviour two",
                    acceptance_criteria=["AC two-a", "AC two-b"],
                ),
            ]
        )
        spec = materialize_ticket_spec_from_capability(
            ticket_id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            capability=cap,
        )
        behaviors = spec.fields["behaviors"]
        assert all(isinstance(b, dict) for b in behaviors)
        assert behaviors[0]["id"] == "b-one"
        assert behaviors[0]["acceptance_criteria"] == ["AC one"]
        assert behaviors[1]["acceptance_criteria"] == ["AC two-a", "AC two-b"]
        # Round-trips through YAML without losing structure.
        roundtripped = yaml.safe_load(yaml.safe_dump(spec.fields))
        assert roundtripped["behaviors"][0]["id"] == "b-one"

    def test_empty_excluded_yields_empty_list(self) -> None:
        cap = _capability(excluded=[])
        spec = materialize_ticket_spec_from_capability(
            ticket_id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            capability=cap,
        )
        # Field must be present (not omitted) so the schema-validation path
        # can detect it as empty rather than missing.
        assert "out_of_scope" in spec.fields
        assert spec.fields["out_of_scope"] == []

    def test_empty_top_level_ac_flattens_behavior_ac(self) -> None:
        """When capability.acceptance_criteria is empty, the materialiser
        flattens every behavior's AC into the top-level list so the work-
        type schema's AC-presence invariant holds."""
        cap = _capability(
            behaviors=[
                Behavior(
                    id="b1",
                    description="x",
                    acceptance_criteria=["from b1 #1", "from b1 #2"],
                ),
                Behavior(
                    id="b2",
                    description="y",
                    acceptance_criteria=["from b2"],
                ),
            ],
            acceptance_criteria=[],
        )
        spec = materialize_ticket_spec_from_capability(
            ticket_id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            capability=cap,
        )
        assert spec.fields["acceptance_criteria"] == [
            "from b1 #1",
            "from b1 #2",
            "from b2",
        ]

    def test_capability_ac_wins_over_behavior_flatten(self) -> None:
        """If the capability has top-level AC, the materialiser uses it
        verbatim and does not also flatten behaviors — otherwise the same
        AC item might appear twice."""
        cap = _capability(
            behaviors=[
                Behavior(
                    id="b1",
                    description="x",
                    acceptance_criteria=["behaviour AC"],
                )
            ],
            acceptance_criteria=["top-level AC"],
        )
        spec = materialize_ticket_spec_from_capability(
            ticket_id="t-1",
            work_type=WorkType.FEATURE,
            size=Size.M,
            capability=cap,
        )
        assert spec.fields["acceptance_criteria"] == ["top-level AC"]

    def test_materialised_spec_round_trips_save_load(
        self, initialized_project: Path
    ) -> None:
        """End-to-end: a materialised spec must satisfy the feature work-
        type schema and survive save_ticket_spec → load_ticket_spec."""
        cap = _capability(
            summary="Pull stories",
            behaviors=[
                Behavior(
                    id="run-top",
                    description="prints stories",
                    acceptance_criteria=["Exit 0", "N lines"],
                )
            ],
            excluded=["pagination"],
        )
        spec = materialize_ticket_spec_from_capability(
            ticket_id="t-cap",
            work_type=WorkType.FEATURE,
            size=Size.M,
            capability=cap,
        )
        save_ticket_spec(initialized_project, spec)
        loaded = load_ticket_spec(initialized_project, "t-cap")
        assert loaded is not None
        assert loaded.fields["summary"] == "Pull stories"
        assert loaded.fields["acceptance_criteria"] == ["Exit 0", "N lines"]
        assert loaded.fields["out_of_scope"] == ["pagination"]


class TestSaveTicketSpecValidateFlag:
    """``enforce_required_fields=False`` is the escape hatch the
    capability materialiser needs to write L/XL feature specs. The
    capability never carries ``design`` or ``technical_risks`` so
    strict validation would reject the spec for sizes beyond M.
    Unknown-field validation still runs to catch misuses."""

    def test_l_capability_spec_saves_without_required_check(
        self, initialized_project: Path
    ) -> None:
        cap = _capability(
            summary="something big",
            behaviors=[
                Behavior(
                    id="b1",
                    description="x",
                    acceptance_criteria=["covers L"],
                )
            ],
            excluded=["a"],
        )
        spec = materialize_ticket_spec_from_capability(
            ticket_id="t-large",
            work_type=WorkType.FEATURE,
            size=Size.L,
            capability=cap,
        )
        # With enforce_required_fields=True (the default) this would
        # raise because the feature schema requires ``design`` at L.
        save_ticket_spec(initialized_project, spec, enforce_required_fields=False)
        loaded = load_ticket_spec(initialized_project, "t-large")
        assert loaded is not None
        assert loaded.size == Size.L
        assert "design" not in loaded.fields
        assert loaded.fields["acceptance_criteria"] == ["covers L"]

    def test_xl_capability_spec_saves_without_required_check(
        self, initialized_project: Path
    ) -> None:
        cap = _capability(
            summary="something huge",
            behaviors=[
                Behavior(
                    id="b1",
                    description="x",
                    acceptance_criteria=["covers XL"],
                )
            ],
            excluded=["a"],
        )
        spec = materialize_ticket_spec_from_capability(
            ticket_id="t-xl",
            work_type=WorkType.FEATURE,
            size=Size.XL,
            capability=cap,
        )
        save_ticket_spec(initialized_project, spec, enforce_required_fields=False)
        loaded = load_ticket_spec(initialized_project, "t-xl")
        assert loaded is not None
        assert loaded.size == Size.XL

    def test_default_still_rejects_l_missing_design(
        self, initialized_project: Path
    ) -> None:
        """Regression guard: only the capability materialiser opts out.
        A direct save_ticket_spec call against an incomplete L spec must
        still fail loudly so the proposal-accept / operator-edit paths
        keep their existing strictness."""
        cap = _capability(
            summary="x",
            behaviors=[
                Behavior(
                    id="b1",
                    description="x",
                    acceptance_criteria=["ok"],
                )
            ],
        )
        spec = materialize_ticket_spec_from_capability(
            ticket_id="t-strict",
            work_type=WorkType.FEATURE,
            size=Size.L,
            capability=cap,
        )
        with pytest.raises(SpecValidationError):
            save_ticket_spec(initialized_project, spec)

    def test_unknown_fields_still_rejected_when_required_check_off(
        self, initialized_project: Path
    ) -> None:
        """``enforce_required_fields=False`` does not bypass the
        unknown-fields check. A spec containing a field the work-type
        schema doesn't declare must still fail loudly — otherwise a
        custom schema or a misrouted materialiser could persist
        feature-shaped fields into an unrelated work-type spec."""
        spec = TicketSpec(
            ticket_id="t-bogus",
            work_type=WorkType.FEATURE,
            size=Size.M,
            fields={
                "summary": "x",
                "behaviors": [
                    {"id": "B1", "description": "x", "acceptance_criteria": ["ac"]}
                ],
                "acceptance_criteria": ["ac"],
                "out_of_scope": ["nope"],
                "field_not_in_schema": "this is not allowed",
            },
        )
        with pytest.raises(SpecValidationError, match="unknown fields"):
            save_ticket_spec(initialized_project, spec, enforce_required_fields=False)
