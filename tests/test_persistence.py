"""Tests for jig.persistence — init_project, agent types, workflows."""

from pathlib import Path

import pytest

from jig.models import RoleConfig, PhaseConfig, WorkflowConfig
from jig.persistence import (
    init_project,
    list_role_names,
    list_roles,
    load_conventions,
    load_role,
    load_workflow,
    resolve_workflow_name,
    save_role,
    save_default_roles,
    save_default_workflow,
    save_workflow,
)


@pytest.fixture
def tmp_new_jig_project(tmp_path: Path) -> Path:
    """A git repo with the new .jig/ layout already initialized."""
    (tmp_path / ".git").mkdir()
    jig_dir = tmp_path / ".jig"
    jig_dir.mkdir()
    for subdir in ("roles", "workflows", "worktrees", "store"):
        (jig_dir / subdir).mkdir()
    return tmp_path


class TestInitProject:
    def test_creates_jig_directory(self, tmp_project: Path) -> None:
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert jig_dir.is_dir()
        assert (jig_dir / "roles").is_dir()
        assert (jig_dir / "workflows").is_dir()
        assert (jig_dir / "worktrees").is_dir()
        assert (jig_dir / "store").is_dir()

    def test_creates_doc17_placeholders(self, tmp_project: Path) -> None:
        """Per doc 17 layout — populated in later phases, laid down now."""
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert (jig_dir / "spec").is_dir()
        assert (jig_dir / "context" / "project").is_dir()
        assert (jig_dir / "context" / "roles").is_dir()
        assert (jig_dir / "decisions").is_dir()
        assert (jig_dir / "archive").is_dir()

    def test_creates_project_spec_stub(self, tmp_project: Path) -> None:
        """`docs/brief.md` ships as a PO-facing template.

        Per docs/02-project-spec.md §"Human format example" the PO's
        brief has state-category level-2 headers. The stub mirrors that
        shape so the PO has somewhere concrete to start writing.
        """
        init_project(tmp_project)
        spec_path = tmp_project / "docs" / "brief.md"
        assert spec_path.is_file()
        body = spec_path.read_text()
        # Every state-category header from doc 02 must be present.
        for header in (
            "## Built",
            "## Planned (committed)",
            "## Planned (not yet committed)",
            "## Backlog",
            "## Non-goals",
        ):
            assert header in body, f"missing {header!r} in brief.md stub"
        # Top-level heading is derived from the project's directory name
        # so a git clone doesn't come with a title that claims to be a
        # different project.
        assert f"# {tmp_project.name}" in body

    def test_creates_empty_checks_catalog(self, tmp_project: Path) -> None:
        init_project(tmp_project)
        checks_path = tmp_project / ".jig" / "checks.yaml"
        assert checks_path.is_file()
        import yaml

        data = yaml.safe_load(checks_path.read_text())
        assert data == {"checks": {}}

    def test_does_not_create_legacy_dirs(self, tmp_project: Path) -> None:
        init_project(tmp_project)
        jig_dir = tmp_project / ".jig"
        assert not (jig_dir / "issues").exists()
        assert not (jig_dir / "config.yaml").exists()
        assert not (jig_dir / "agents").exists()

    def test_raises_if_already_initialized(self, tmp_new_jig_project: Path) -> None:
        with pytest.raises(FileExistsError):
            init_project(tmp_new_jig_project)

    def test_raises_if_not_git_repo(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not a git repository"):
            init_project(tmp_path)


class TestAgentTypePersistence:
    def test_save_and_load(self, tmp_new_jig_project: Path) -> None:
        config = RoleConfig(
            role="dev",
            phase_prompt="You are a dev agent.",
            allowed_tools=["Read", "Edit"],
        )
        save_role(tmp_new_jig_project, config)
        loaded = load_role(tmp_new_jig_project, "dev")
        assert loaded.role == "dev"
        assert loaded.phase_prompt == "You are a dev agent."
        assert loaded.allowed_tools == ["Read", "Edit"]

    def test_saves_to_correct_path(self, tmp_new_jig_project: Path) -> None:
        config = RoleConfig(role="test", phase_prompt="Test agent.")
        save_role(tmp_new_jig_project, config)
        yaml_path = tmp_new_jig_project / ".jig" / "roles" / "test.yaml"
        assert yaml_path.is_file()

    def test_list_empty_falls_back_to_shipped_defaults(
        self, tmp_new_jig_project: Path
    ) -> None:
        """Phase 2 Task C: project override layer empty → serve shipped defaults."""
        types = list_roles(tmp_new_jig_project)
        roles = {t.role for t in types}
        # The exact set of shipped defaults is asserted in TestDefaultRoles.
        # Here we just need to know the fallback layer is in play.
        assert roles, "list_roles should fall back to shipped defaults"
        assert "dev" in roles

    def test_list_merges_project_override_with_shipped(
        self, tmp_new_jig_project: Path
    ) -> None:
        save_role(
            tmp_new_jig_project,
            RoleConfig(role="dev", phase_prompt="Project dev override."),
        )
        save_role(
            tmp_new_jig_project,
            RoleConfig(role="custom-role", phase_prompt="Project only."),
        )
        types = {t.role: t for t in list_roles(tmp_new_jig_project)}
        # Shipped defaults show up.
        assert "pm" in types
        assert "spec" in types
        # Project-only role is present.
        assert "custom-role" in types
        # Project override wins for shared names.
        assert types["dev"].phase_prompt == "Project dev override."

    def test_load_role_prefers_project_override(
        self, tmp_new_jig_project: Path
    ) -> None:
        save_role(
            tmp_new_jig_project,
            RoleConfig(role="dev", phase_prompt="Custom project dev."),
        )
        loaded = load_role(tmp_new_jig_project, "dev")
        assert loaded.phase_prompt == "Custom project dev."

    def test_load_role_falls_back_to_shipped_default(
        self, tmp_new_jig_project: Path
    ) -> None:
        """Project override missing → serve shipped default transparently."""
        loaded = load_role(tmp_new_jig_project, "dev")
        # dev.yaml ships in jig/defaults/roles/ — should resolve.
        assert loaded.role == "dev"

    def test_load_nonexistent_raises(self, tmp_new_jig_project: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_role(tmp_new_jig_project, "nope")

    def test_reads_glob_round_trips(self, tmp_new_jig_project: Path) -> None:
        """Reviewer file-scoping fields (``reads_glob`` /
        ``reads_exclude``) persist through ``save_role`` /
        ``load_role`` with their list shape preserved.

        Defaults to empty lists when unset so legacy role configs
        without the fields load cleanly — verified via the existing
        round-trip tests above.
        """
        cfg = RoleConfig(
            role="reviewer-pattern-conformance",
            phase_prompt="…",
            reads_glob=["src/**", "pyproject.toml"],
            reads_exclude=["tests/**", "**/conftest.py"],
        )
        save_role(tmp_new_jig_project, cfg)
        loaded = load_role(
            tmp_new_jig_project, "reviewer-pattern-conformance"
        )
        assert loaded.reads_glob == ["src/**", "pyproject.toml"]
        assert loaded.reads_exclude == ["tests/**", "**/conftest.py"]

    def test_reads_glob_default_empty(
        self, tmp_new_jig_project: Path
    ) -> None:
        """Roles that don't set the new fields keep the empty-list
        default — i.e. the role operates unscoped, matching legacy
        behaviour."""
        cfg = RoleConfig(role="dev", phase_prompt="…")
        save_role(tmp_new_jig_project, cfg)
        loaded = load_role(tmp_new_jig_project, "dev")
        assert loaded.reads_glob == []
        assert loaded.reads_exclude == []


class TestDefaultRoles:
    def test_creates_all_types(self, tmp_new_jig_project: Path) -> None:
        save_default_roles(tmp_new_jig_project)
        types = list_roles(tmp_new_jig_project)
        roles = {t.role for t in types}
        # ``user`` is a shipped pseudo-role (no phase_prompt, no allowed_tools)
        # — never dispatched to Claude Code; carries default waiver capability
        # for future user-driven waive flows. It surfaces via the shipped
        # fallthrough in ``list_roles`` even when not explicitly copied.
        assert roles == {
            "spec",
            "test",
            "dev",
            "review",
            "validate",
            "document",
            "pm",
            "user",
            "po",
            "po-l0",
            "po-l1",
            "po-l2",
            "po-l3",
            "sa",
            "sa-v2",
            "sa-mvp",
            "planner-pm",
            "spec-generator",
            "concierge",
            "quartermaster",
            # Track G MVP follow-on judgment reviewers — LLM-driven.
            "reviewer-pattern-conformance",
            "reviewer-error-handling",
            "reviewer-test-adequacy",
            # Track G Final specialty reviewers — LLM-driven, gated
            # by ticket characteristics in
            # ``jig.reviewers.dispatch.select_reviewers_for_ticket``.
            "reviewer-security",
            "reviewer-performance",
            "reviewer-architectural",
            # Single-pass generalist used by small/medium workflows in
            # place of the specialist federation.
            "reviewer-generalist",
            # Track D MVP — VD (Visual Designer / frontend architect).
            "vd",
            # Conflict resolver — spawned by the orchestrator to fix merge conflicts.
            "conflict_resolver",
            # Canonicalizer — runs formatters / semgrep / deprecations after merge.
            "canonicalizer",
        }

    def test_each_has_phase_prompt(self, tmp_new_jig_project: Path) -> None:
        save_default_roles(tmp_new_jig_project)
        for name in ("spec", "test", "dev", "review", "validate", "document", "pm"):
            config = load_role(tmp_new_jig_project, name)
            assert len(config.phase_prompt) > 0

    def test_each_has_allowed_tools(self, tmp_new_jig_project: Path) -> None:
        save_default_roles(tmp_new_jig_project)
        for name in ("spec", "test", "dev", "review", "validate", "document", "pm"):
            config = load_role(tmp_new_jig_project, name)
            assert len(config.allowed_tools) > 0

    def test_each_has_default_context(self, tmp_new_jig_project: Path) -> None:
        save_default_roles(tmp_new_jig_project)
        for name in ("spec", "test", "dev", "review"):
            config = load_role(tmp_new_jig_project, name)
            assert len(config.default_context) > 0


# Shipped role IDs whose underlying YAML file stem differs from
# ``role:``. ``list_role_names`` must surface these by their
# canonical ``role:`` id, not by the file stem, or downstream
# lookups against ``phase.role`` / hard-coded role ids miss them.
_MISMATCHED_SHIPPED_ROLE_IDS = [
    "po-l0",
    "po-l1",
    "po-l2",
    "po-l3",
    "sa-mvp",
    "sa-v2",
    "planner-pm",
    "reviewer-pattern-conformance",
    "reviewer-error-handling",
    "reviewer-test-adequacy",
    "reviewer-security",
    "reviewer-performance",
    "reviewer-architectural",
    "reviewer-generalist",
]


class TestListRoleNamesUsesRoleId:
    @pytest.mark.parametrize("role_id", _MISMATCHED_SHIPPED_ROLE_IDS)
    def test_shipped_role_resolves_by_role_id(
        self, tmp_new_jig_project: Path, role_id: str
    ) -> None:
        assert role_id in list_role_names(tmp_new_jig_project)

    def test_matches_list_roles_projection(self, tmp_new_jig_project: Path) -> None:
        assert set(list_role_names(tmp_new_jig_project)) == {
            r.role for r in list_roles(tmp_new_jig_project)
        }


class TestWorkflowPersistence:
    def test_save_and_load(self, tmp_new_jig_project: Path) -> None:
        workflow = WorkflowConfig(
            name="custom",
            phases=[
                PhaseConfig(name="spec", role="spec"),
                PhaseConfig(name="test", role="test"),
            ],
        )
        save_workflow(tmp_new_jig_project, workflow)
        loaded = load_workflow(tmp_new_jig_project, "custom")
        assert loaded.name == "custom"
        assert len(loaded.phases) == 2

    def test_saves_to_correct_path(self, tmp_new_jig_project: Path) -> None:
        workflow = WorkflowConfig(
            name="custom",
            phases=[PhaseConfig(name="spec", role="spec")],
        )
        save_workflow(tmp_new_jig_project, workflow)
        path = tmp_new_jig_project / ".jig" / "workflows" / "custom.yaml"
        assert path.is_file()

    def test_load_nonexistent_raises(self, tmp_new_jig_project: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_workflow(tmp_new_jig_project, "nope")

    def test_load_workflow_falls_back_to_shipped_default(
        self, tmp_new_jig_project: Path
    ) -> None:
        """Project override missing → serve shipped default."""
        workflow = load_workflow(tmp_new_jig_project, "default")
        assert workflow.name == "default"

    def test_load_workflow_prefers_project_override(
        self, tmp_new_jig_project: Path
    ) -> None:
        save_workflow(
            tmp_new_jig_project,
            WorkflowConfig(
                name="default",
                phases=[PhaseConfig(name="only", role="dev")],
            ),
        )
        loaded = load_workflow(tmp_new_jig_project, "default")
        assert [p.name for p in loaded.phases] == ["only"]

    def test_load_workflow_with_unknown_reviewer_rejected(
        self, tmp_new_jig_project: Path
    ) -> None:
        """Each name in ``reviewers:`` must resolve to a known reviewer
        id. Typos and stale names fail loud at load time, naming the
        offending value."""
        save_workflow(
            tmp_new_jig_project,
            WorkflowConfig(
                name="bad-reviewer-name",
                phases=[
                    PhaseConfig(
                        name="review",
                        role="review",
                        reviewers=["reviewer-typo-not-real"],
                    ),
                ],
            ),
        )
        with pytest.raises(ValueError, match="reviewer-typo-not-real"):
            load_workflow(tmp_new_jig_project, "bad-reviewer-name")

    def test_load_workflow_with_known_reviewers_resolves(
        self, tmp_new_jig_project: Path
    ) -> None:
        """A workflow listing valid reviewer ids loads cleanly."""
        save_workflow(
            tmp_new_jig_project,
            WorkflowConfig(
                name="good-review",
                phases=[
                    PhaseConfig(
                        name="review-tests",
                        role="review",
                        reviewers=["reviewer-test-adequacy"],
                    ),
                ],
            ),
        )
        loaded = load_workflow(tmp_new_jig_project, "good-review")
        assert loaded.phases[0].reviewers == ["reviewer-test-adequacy"]


class TestDefaultWorkflow:
    def test_creates_default(self, tmp_new_jig_project: Path) -> None:
        save_default_workflow(tmp_new_jig_project)
        workflow = load_workflow(tmp_new_jig_project, "default")
        assert workflow.name == "default"
        phase_names = [p.name for p in workflow.phases]
        # The spec phase was removed (deterministic-ticket-spec): the AC is
        # materialised at ticket creation time from project.structured.yaml.
        assert phase_names == [
            "test",
            "review-tests",
            "implement",
            "review",
            "validate",
            "document",
        ]

    def test_roles_correct(self, tmp_new_jig_project: Path) -> None:
        save_default_workflow(tmp_new_jig_project)
        workflow = load_workflow(tmp_new_jig_project, "default")
        roles = {p.name: p.role for p in workflow.phases}
        assert roles == {
            "test": "test",
            "review-tests": "review",
            "implement": "dev",
            "review": "review",
            "validate": "validate",
            "document": "document",
        }

    def test_review_tests_phase_runs_only_test_adequacy(
        self, tmp_new_jig_project: Path
    ) -> None:
        """review-tests fires reviewer-test-adequacy only — the federation
        for the new phase is scoped to test quality."""
        workflow = load_workflow(tmp_new_jig_project, "default")
        review_tests = next(p for p in workflow.phases if p.name == "review-tests")
        assert review_tests.reviewers == ["reviewer-test-adequacy"]

    def test_end_of_ticket_review_excludes_test_adequacy(
        self, tmp_new_jig_project: Path
    ) -> None:
        """End-of-ticket review fires the remaining five LLM reviewers.
        test-adequacy already ran at review-tests and the test files are
        byte-identical at end-of-ticket (TDD lock), so re-running it is
        duplicated work."""
        workflow = load_workflow(tmp_new_jig_project, "default")
        review = next(
            p for p in workflow.phases if p.name == "review" and p.role == "review"
        )
        assert "reviewer-test-adequacy" not in review.reviewers
        # The other five LLM reviewers are listed.
        assert set(review.reviewers) == {
            "reviewer-pattern-conformance",
            "reviewer-architectural",
            "reviewer-error-handling",
            "reviewer-performance",
            "reviewer-security",
        }

    def test_writes_declared_on_writing_phases(self, tmp_new_jig_project: Path) -> None:
        """test, implement, document declare ``writes:``. The
        router uses these to map a file → owning phase."""
        workflow = load_workflow(tmp_new_jig_project, "default")
        writes = {p.name: p.writes for p in workflow.phases}
        # Specific globs locked in so a regression in default.yaml
        # surfaces on this test, not at routing time.
        assert "tests/**" in writes["test"]
        assert "src/**" in writes["implement"]
        assert "pyproject.toml" in writes["implement"]
        assert "docs/**" in writes["document"]


class TestLoadWorkflowReviewRoleRequiresReviewers:
    """Step 6 of feature-work/review-routing/plan.md: the strict
    "role=='review' requires non-empty reviewers" rule is now active.
    Step 2 deferred it because default.yaml didn't satisfy the rule yet."""

    def test_review_phase_without_reviewers_rejected(
        self, tmp_new_jig_project: Path
    ) -> None:
        save_workflow(
            tmp_new_jig_project,
            WorkflowConfig(
                name="bad-review",
                phases=[
                    PhaseConfig(name="spec", role="spec"),
                    PhaseConfig(name="review", role="review"),
                ],
            ),
        )
        # Error message names workflow + phase + the missing field so
        # the operator can fix the YAML directly.
        with pytest.raises(ValueError, match=r"bad-review.*'review'.*reviewers"):
            load_workflow(tmp_new_jig_project, "bad-review")

    def test_second_review_phase_without_reviewers_rejected(
        self, tmp_new_jig_project: Path
    ) -> None:
        """The validator must walk every phase, not just the first.
        A workflow whose FIRST review phase is fine but a later one
        is empty should still fail at load time, naming the offending
        phase."""
        save_workflow(
            tmp_new_jig_project,
            WorkflowConfig(
                name="two-reviews",
                phases=[
                    PhaseConfig(name="test", role="test"),
                    PhaseConfig(
                        name="review-tests",
                        role="review",
                        reviewers=["reviewer-test-adequacy"],
                    ),
                    PhaseConfig(name="implement", role="dev"),
                    # No reviewers — should fail load.
                    PhaseConfig(name="review", role="review"),
                ],
            ),
        )
        with pytest.raises(ValueError, match=r"two-reviews.*'review'.*reviewers"):
            load_workflow(tmp_new_jig_project, "two-reviews")


class TestLoadConventions:
    def test_returns_none_when_file_missing(self, tmp_new_jig_project: Path) -> None:
        assert load_conventions(tmp_new_jig_project) is None

    def test_returns_none_when_file_empty(self, tmp_new_jig_project: Path) -> None:
        (tmp_new_jig_project / ".jig" / "conventions.md").write_text("   \n")
        assert load_conventions(tmp_new_jig_project) is None

    def test_returns_content_when_present(self, tmp_new_jig_project: Path) -> None:
        content = "# Conventions\n\n- Use httpx for HTTP."
        (tmp_new_jig_project / ".jig" / "conventions.md").write_text(content)
        assert load_conventions(tmp_new_jig_project) == content

    def test_strips_trailing_whitespace(self, tmp_new_jig_project: Path) -> None:
        (tmp_new_jig_project / ".jig" / "conventions.md").write_text("rule one\n\n\n")
        assert load_conventions(tmp_new_jig_project) == "rule one"


class TestResolveWorkflowName:
    def test_explicit_ticket_workflow_takes_precedence(
        self, tmp_new_jig_project: Path
    ) -> None:
        result = resolve_workflow_name(tmp_new_jig_project, "my-custom", "feature", "m")
        assert result == "my-custom"

    def test_default_string_falls_through_to_work_type(
        self, tmp_new_jig_project: Path
    ) -> None:
        # "default" ticket_workflow should resolve via work_type schema.
        result = resolve_workflow_name(tmp_new_jig_project, "default", "feature", "xs")
        assert result == "feature-xs"

    def test_workflow_by_size_s(self, tmp_new_jig_project: Path) -> None:
        result = resolve_workflow_name(tmp_new_jig_project, "default", "feature", "s")
        assert result == "feature-s"

    def test_workflow_by_size_m_uses_schema_default(
        self, tmp_new_jig_project: Path
    ) -> None:
        # Size "m" maps to "default" explicitly in the feature schema.
        result = resolve_workflow_name(tmp_new_jig_project, "default", "feature", "m")
        assert result == "default"

    def test_unknown_size_falls_back_to_schema_workflow(
        self, tmp_new_jig_project: Path
    ) -> None:
        result = resolve_workflow_name(
            tmp_new_jig_project, "default", "feature", "unknown"
        )
        assert result == "default"  # schema.workflow fallback

    def test_missing_work_type_returns_default(self, tmp_new_jig_project: Path) -> None:
        result = resolve_workflow_name(
            tmp_new_jig_project, "default", "nonexistent_type", None
        )
        assert result == "default"

    def test_empty_ticket_workflow_falls_through(
        self, tmp_new_jig_project: Path
    ) -> None:
        result = resolve_workflow_name(tmp_new_jig_project, "", "feature", "xs")
        assert result == "feature-xs"

    def test_no_work_type_returns_default(self, tmp_new_jig_project: Path) -> None:
        result = resolve_workflow_name(tmp_new_jig_project, "default", None, None)
        assert result == "default"
