"""Tests for jig.capabilities (declaration + merge) and
jig.capability_compiler (compile + materialize).

These are Phase 5 Task F — the schema + the pure compile step + the
file-writer. Task G adds the enforcement hooks that actually read the
emitted rules.json. Runtime wiring (agent.py calling materialize before
spawning a Claude Code process) is covered by
tests/test_agent_streaming.py::TestMaterializeCapabilityPolicy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from jig.capabilities import (
    WAIVE_TOKENS,
    BashToolParams,
    CapabilityDeclaration,
    CapabilityPaths,
    CapabilityToolParams,
    CapabilityTools,
    CapabilityWaivers,
    is_known_waive_token,
    merge_declarations,
)
from jig.capability_compiler import (
    SANDBOX_HOOK_BIN,
    SCHEMA_VERSION,
    CompiledWaiverRules,
    compile,
    materialize,
    write_claude_settings,
    write_rules_json,
)


# ---- declaration shape ----------------------------------------------------


class TestDeclarationConstruction:
    def test_empty_declaration_valid(self) -> None:
        decl = CapabilityDeclaration()
        assert decl.tools is None
        assert decl.tool_params is None
        assert decl.paths is None
        assert decl.waivers is None

    def test_full_declaration_valid(self) -> None:
        decl = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Read", "Write"]),
            tool_params=CapabilityToolParams(
                Bash=BashToolParams(deny_patterns=["rm -rf"])
            ),
            paths=CapabilityPaths(
                writable=["ticket://worktree/**"],
                readable=["repo://**"],
                denied=[".jig/spec/**"],
            ),
        )
        assert decl.tools is not None
        assert decl.tools.allowed == ["Read", "Write"]
        assert decl.tool_params is not None
        assert decl.tool_params.Bash is not None
        assert decl.tool_params.Bash.deny_patterns == ["rm -rf"]
        assert decl.paths is not None
        assert decl.paths.denied == [".jig/spec/**"]

    def test_unknown_top_level_key_rejected(self) -> None:
        """``extra="forbid"`` means a typo like ``capabilites:`` (sic)
        fails loud at load — silent ignores are the kind of thing that
        lets a security rule quietly not apply."""
        with pytest.raises(ValidationError):
            CapabilityDeclaration.model_validate(
                {"tools": {"allowed": []}, "unknownKey": 1}
            )

    def test_unknown_tool_param_key_rejected(self) -> None:
        """A typo like ``bash:`` (lowercase) would silently produce no
        constraints if we accepted extras. Forbid them."""
        with pytest.raises(ValidationError):
            CapabilityToolParams.model_validate({"bash": {"deny_patterns": ["rm -rf"]}})

    def test_paths_category_keys_typed(self) -> None:
        with pytest.raises(ValidationError):
            CapabilityPaths.model_validate(
                {"writable": ["x"], "readonly": ["y"]}  # "readable" misspelled
            )


class TestCapabilityWaivers:
    def test_empty_waivers_valid(self) -> None:
        w = CapabilityWaivers()
        assert w.can_waive == []

    def test_waivers_accepts_all_known_tokens(self) -> None:
        w = CapabilityWaivers(can_waive=sorted(WAIVE_TOKENS))
        assert set(w.can_waive) == WAIVE_TOKENS

    def test_waivers_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            CapabilityWaivers.model_validate(
                {"can_waive": ["objection"], "extra": "nope"}
            )

    def test_known_waive_tokens_registry(self) -> None:
        assert "objection" in WAIVE_TOKENS
        assert "check_failure:required" in WAIVE_TOKENS
        assert "check_failure:warning" in WAIVE_TOKENS
        assert is_known_waive_token("objection") is True
        assert is_known_waive_token("check_failure:warning") is True
        assert is_known_waive_token("bogus") is False

    def test_declaration_accepts_waivers_field(self) -> None:
        decl = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        assert decl.waivers is not None
        assert decl.waivers.can_waive == ["objection"]

    def test_declaration_waivers_defaults_none(self) -> None:
        decl = CapabilityDeclaration()
        assert decl.waivers is None


# ---- merge_declarations ---------------------------------------------------


class TestMergeDeclarations:
    def test_both_none_yields_empty_declaration(self) -> None:
        merged = merge_declarations(None, None)
        assert merged.tools is None
        assert merged.tool_params is None
        assert merged.paths is None
        assert merged.waivers is None

    def test_base_only_preserved(self) -> None:
        base = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Read"]),
            paths=CapabilityPaths(writable=["ticket://worktree/**"]),
        )
        merged = merge_declarations(base, None)
        assert merged.tools is not None
        assert merged.tools.allowed == ["Read"]
        assert merged.paths is not None
        assert merged.paths.writable == ["ticket://worktree/**"]

    def test_override_only_preserved(self) -> None:
        over = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Bash"]),
        )
        merged = merge_declarations(None, over)
        assert merged.tools is not None
        assert merged.tools.allowed == ["Bash"]

    def test_allowed_unioned_preserves_order(self) -> None:
        base = CapabilityDeclaration(tools=CapabilityTools(allowed=["Read", "Write"]))
        over = CapabilityDeclaration(tools=CapabilityTools(allowed=["Write", "Bash"]))
        merged = merge_declarations(base, over)
        assert merged.tools is not None
        # First-seen order preserved: Read, Write (from base), then Bash.
        assert merged.tools.allowed == ["Read", "Write", "Bash"]

    def test_deny_patterns_unioned(self) -> None:
        base = CapabilityDeclaration(
            tool_params=CapabilityToolParams(
                Bash=BashToolParams(deny_patterns=["rm -rf"])
            )
        )
        over = CapabilityDeclaration(
            tool_params=CapabilityToolParams(
                Bash=BashToolParams(deny_patterns=["git push --force", "rm -rf"])
            )
        )
        merged = merge_declarations(base, over)
        assert merged.tool_params is not None
        assert merged.tool_params.Bash is not None
        # Union de-dupes while preserving first-seen order.
        assert merged.tool_params.Bash.deny_patterns == [
            "rm -rf",
            "git push --force",
        ]

    def test_paths_all_categories_unioned(self) -> None:
        base = CapabilityDeclaration(
            paths=CapabilityPaths(
                writable=["ticket://worktree/**"],
                readable=["repo://**"],
                denied=[".jig/spec/**"],
            )
        )
        over = CapabilityDeclaration(
            paths=CapabilityPaths(
                writable=["ticket://worktree/docs/**"],
                denied=[".jig/decisions/**"],
            )
        )
        merged = merge_declarations(base, over)
        assert merged.paths is not None
        assert merged.paths.writable == [
            "ticket://worktree/**",
            "ticket://worktree/docs/**",
        ]
        assert merged.paths.readable == ["repo://**"]
        assert merged.paths.denied == [
            ".jig/spec/**",
            ".jig/decisions/**",
        ]

    def test_override_adds_new_category_without_base(self) -> None:
        """Phase declares paths the role didn't; merge should expose
        the override's paths intact rather than dropping them because
        base had ``paths is None``."""
        base = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Read"]),
        )
        over = CapabilityDeclaration(
            paths=CapabilityPaths(writable=["ticket://worktree/**"]),
        )
        merged = merge_declarations(base, over)
        assert merged.paths is not None
        assert merged.paths.writable == ["ticket://worktree/**"]
        assert merged.tools is not None
        assert merged.tools.allowed == ["Read"]

    def test_merge_waivers_base_only(self) -> None:
        base = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        merged = merge_declarations(base, None)
        assert merged.waivers is not None
        assert merged.waivers.can_waive == ["objection"]

    def test_merge_waivers_override_only(self) -> None:
        override = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["check_failure:warning"])
        )
        merged = merge_declarations(None, override)
        assert merged.waivers is not None
        assert merged.waivers.can_waive == ["check_failure:warning"]

    def test_merge_waivers_unions_both(self) -> None:
        base = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        override = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["check_failure:warning"])
        )
        merged = merge_declarations(base, override)
        assert merged.waivers is not None
        assert merged.waivers.can_waive == [
            "objection",
            "check_failure:warning",
        ]

    def test_merge_waivers_dedups(self) -> None:
        base = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        override = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        merged = merge_declarations(base, override)
        assert merged.waivers is not None
        assert merged.waivers.can_waive == ["objection"]

    def test_merge_waivers_neither_declared(self) -> None:
        base = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Read"])
        )
        override = CapabilityDeclaration()
        merged = merge_declarations(base, override)
        assert merged.waivers is None

    def test_merge_waivers_independent_of_other_fields(self) -> None:
        base = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Read"]),
            waivers=CapabilityWaivers(can_waive=["objection"]),
        )
        override = CapabilityDeclaration(
            paths=CapabilityPaths(writable=["ticket://worktree/**"]),
        )
        merged = merge_declarations(base, override)
        assert merged.tools is not None
        assert merged.tools.allowed == ["Read"]
        assert merged.paths is not None
        assert merged.paths.writable == ["ticket://worktree/**"]
        assert merged.waivers is not None
        assert merged.waivers.can_waive == ["objection"]


# ---- compile() ------------------------------------------------------------


class TestCompile:
    def test_compile_none_yields_empty_rules(self) -> None:
        rules = compile(None, None)
        assert rules.schema_version == SCHEMA_VERSION
        assert rules.tools.allowed == []
        assert rules.bash.deny_patterns == []
        assert rules.paths.writable == []
        assert rules.paths.readable == []
        assert rules.paths.denied == []

    def test_compile_full_declaration(self) -> None:
        role = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Read", "Write", "Bash"]),
            tool_params=CapabilityToolParams(
                Bash=BashToolParams(deny_patterns=["rm -rf"])
            ),
            paths=CapabilityPaths(
                writable=["ticket://worktree/**"],
                readable=["repo://**"],
                denied=[".jig/spec/**"],
            ),
        )
        rules = compile(role, None)
        assert rules.tools.allowed == ["Read", "Write", "Bash"]
        assert rules.bash.deny_patterns == ["rm -rf"]
        assert rules.paths.writable == ["ticket://worktree/**"]
        assert rules.paths.readable == ["repo://**"]
        assert rules.paths.denied == [".jig/spec/**"]

    def test_compile_merges_role_and_phase(self) -> None:
        role = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Read"]),
            paths=CapabilityPaths(denied=[".jig/spec/**"]),
        )
        phase = CapabilityDeclaration(
            tools=CapabilityTools(allowed=["Write"]),
            paths=CapabilityPaths(denied=[".jig/decisions/**"]),
        )
        rules = compile(role, phase)
        assert rules.tools.allowed == ["Read", "Write"]
        # Denies stack.
        assert rules.paths.denied == [
            ".jig/spec/**",
            ".jig/decisions/**",
        ]

    def test_schema_version_is_2(self) -> None:
        assert SCHEMA_VERSION == 2

    def test_compile_waivers_empty_when_undeclared(self) -> None:
        rules = compile(None, None)
        assert isinstance(rules.waivers, CompiledWaiverRules)
        assert rules.waivers.can_waive == []

    def test_compile_propagates_waivers(self) -> None:
        base = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["objection"])
        )
        override = CapabilityDeclaration(
            waivers=CapabilityWaivers(can_waive=["check_failure:warning"])
        )
        rules = compile(base, override)
        assert rules.waivers.can_waive == [
            "objection",
            "check_failure:warning",
        ]

    def test_compile_output_carries_schema_2(self) -> None:
        rules = compile(None, None)
        assert rules.schema_version == 2


# ---- materialisation ------------------------------------------------------


class TestWriteRulesJson:
    def test_writes_well_formed_json(self, tmp_path: Path) -> None:
        rules = compile(
            CapabilityDeclaration(tools=CapabilityTools(allowed=["Read", "Write"])),
            None,
        )
        target = tmp_path / "policy" / "rules.json"
        write_rules_json(rules, target)

        assert target.exists()
        parsed = json.loads(target.read_text())
        assert parsed["schema_version"] == SCHEMA_VERSION
        assert parsed["tools"]["allowed"] == ["Read", "Write"]
        # Trailing newline — keeps editors happy, avoids git warnings.
        assert target.read_text().endswith("\n")

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c" / "rules.json"
        write_rules_json(compile(None, None), target)
        assert target.exists()

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "rules.json"
        target.write_text("stale content")
        write_rules_json(compile(None, None), target)
        parsed = json.loads(target.read_text())
        assert parsed["schema_version"] == SCHEMA_VERSION

    def test_rules_json_carries_waivers(self, tmp_path: Path) -> None:
        decl = CapabilityDeclaration(
            waivers=CapabilityWaivers(
                can_waive=["objection", "check_failure:required"]
            )
        )
        rules = compile(decl, None)
        path = tmp_path / "rules.json"
        write_rules_json(rules, path)
        data = json.loads(path.read_text())
        assert data["schema_version"] == 2
        assert data["waivers"] == {
            "can_waive": ["objection", "check_failure:required"]
        }


class TestWriteClaudeSettings:
    def test_empty_rules_produce_no_hooks(self, tmp_path: Path) -> None:
        """A spawn with no capability rules shouldn't register hooks —
        a hook that has nothing to enforce just wastes a subprocess per
        tool call."""
        target = tmp_path / ".claude" / "settings.json"
        write_claude_settings(compile(None, None), target)
        parsed = json.loads(target.read_text())
        assert parsed == {"hooks": {}}

    def test_bash_deny_registers_bash_hook_only(self, tmp_path: Path) -> None:
        rules = compile(
            CapabilityDeclaration(
                tool_params=CapabilityToolParams(
                    Bash=BashToolParams(deny_patterns=["rm -rf"])
                )
            ),
            None,
        )
        target = tmp_path / "settings.json"
        write_claude_settings(rules, target)
        parsed = json.loads(target.read_text())
        matchers = parsed["hooks"]["PreToolUse"]
        assert len(matchers) == 1
        assert matchers[0]["matcher"] == "Bash"
        assert matchers[0]["hooks"][0]["command"] == f"{SANDBOX_HOOK_BIN}/check-bash"

    def test_writable_paths_register_write_hook(self, tmp_path: Path) -> None:
        rules = compile(
            CapabilityDeclaration(
                paths=CapabilityPaths(writable=["ticket://worktree/**"])
            ),
            None,
        )
        target = tmp_path / "settings.json"
        write_claude_settings(rules, target)
        matchers = json.loads(target.read_text())["hooks"]["PreToolUse"]
        commands = [m["hooks"][0]["command"] for m in matchers]
        assert f"{SANDBOX_HOOK_BIN}/check-write" in commands
        # Only writable declared — no readable, no denied → read hook
        # should not register.
        assert f"{SANDBOX_HOOK_BIN}/check-path" not in commands

    def test_denied_paths_register_both_read_and_write_hooks(
        self, tmp_path: Path
    ) -> None:
        """A pure-deny declaration (no writable, no readable) needs both
        hooks because denies apply on read and write paths alike."""
        rules = compile(
            CapabilityDeclaration(paths=CapabilityPaths(denied=[".jig/spec/**"])),
            None,
        )
        target = tmp_path / "settings.json"
        write_claude_settings(rules, target)
        matchers = json.loads(target.read_text())["hooks"]["PreToolUse"]
        commands = [m["hooks"][0]["command"] for m in matchers]
        assert f"{SANDBOX_HOOK_BIN}/check-write" in commands
        assert f"{SANDBOX_HOOK_BIN}/check-path" in commands

    def test_full_declaration_registers_all_three(self, tmp_path: Path) -> None:
        rules = compile(
            CapabilityDeclaration(
                tool_params=CapabilityToolParams(
                    Bash=BashToolParams(deny_patterns=["rm -rf"])
                ),
                paths=CapabilityPaths(
                    writable=["ticket://worktree/**"],
                    readable=["repo://**"],
                    denied=[".jig/spec/**"],
                ),
            ),
            None,
        )
        target = tmp_path / "settings.json"
        write_claude_settings(rules, target)
        matchers = json.loads(target.read_text())["hooks"]["PreToolUse"]
        commands = sorted(m["hooks"][0]["command"] for m in matchers)
        assert commands == sorted(
            [
                f"{SANDBOX_HOOK_BIN}/check-bash",
                f"{SANDBOX_HOOK_BIN}/check-write",
                f"{SANDBOX_HOOK_BIN}/check-path",
            ]
        )


class TestMaterialize:
    def test_writes_both_files_to_expected_layout(self, tmp_path: Path) -> None:
        worktree = tmp_path / "worktree"
        policy = tmp_path / "policy"
        rules = compile(
            CapabilityDeclaration(
                tools=CapabilityTools(allowed=["Read"]),
                paths=CapabilityPaths(denied=[".jig/spec/**"]),
            ),
            None,
        )
        rules_path, settings_path = materialize(
            rules, worktree_path=worktree, policy_dir=policy
        )
        # Rules live outside the worktree; settings.json must land under
        # .claude/ for Claude Code to discover it relative to cwd.
        assert rules_path == policy / "rules.json"
        assert settings_path == worktree / ".claude" / "settings.json"
        assert rules_path.exists()
        assert settings_path.exists()
