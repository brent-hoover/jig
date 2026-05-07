### Critical findings

1. `jig/ticket_mcp.py:536`: `handle_request_context` joins `worktree_path / args["path"]` and reads it without resolving/containment checks. An agent can request `../../...` and read outside its worktree. This matters because this is an MCP read surface exposed to agents. Suggested fix: resolve the target, require it to stay under `worktree_path.resolve()`, and reject absolute paths / `..` escapes.

2. `jig/worktree.py:60`: worktree paths and branch names are built directly from `ticket_id`; `jig/ticket.py:76` does not validate ticket ids. A malicious or malformed plan ticket id can escape `.jig/worktrees` or create invalid/dangerous git refs. Suggested fix: validate ticket ids at `Ticket` construction and at plan-finalize/materialization with a strict ref/path-safe id pattern.

3. `jig/dev_env/orchestrator_hook.py:148` and `jig/orchestrator.py:224`: `build_fixture_env()` exists, but `_run_agent_with_analytics` never calls it. Real spawned agents never receive `JIG_FIXTURE_MODE`, so the "replay_only by default, record_new for spikes" safety contract is not active in real mode. Suggested fix: merge `build_fixture_env(ctx.ticket)` into `ctx.extra_env` alongside `JIG_DEV_*_URL`.

4. `jig/dev_env/provisioning.py:271` and `jig/dev_env/orchestrator_hook.py:91`: SQLite `per_agent_ephemeral` only registers when `ProvisioningRegistry(project_root=...)` is used, but real orchestrator provisioning calls `provision_agent_namespace(..., registry=None)`, which constructs `ProvisioningRegistry()` without `project_root`. Real agent spawns silently skip SQLite ephemeral services. Suggested fix: have `provision_for_agent` create a registry with `project_root=project_path`, or pass an orchestrator-owned registry.

5. `jig/dev_env/fixtures.py:73` and `jig/spec_loader.py:639`: service/screen/module-style ids are used in file paths without centralized safe-path validation. `FixtureStore.record()` can write `.jig/dev/fixtures/<service_id>.jsonl`; `save_wireframe()` can write `.jig/spec/wireframes/<screen_id>.html`; neither rejects `../`. Suggested fix: introduce one path-segment validator and use it before every id-derived path, plus final `resolve().is_relative_to(root)` checks.

### Important findings

1. `jig/reviewers/dispatch.py:224` and `jig/reviewers/dispatch.py:426`: specialty reviewers are selected (`reviewer-security`, `reviewer-performance`, `reviewer-architectural`) but `dispatch_for_cadence` has no execution branches for them. The federation can report that a reviewer is in the set without ever spawning/running it.

2. `jig/reviewers/dispatch.py:422`: end-of-ticket dispatch calls `select_reviewers_for_ticket(ticket)` without `project_root`, so architecture-driven specialty triggers such as SA-tier modules and perf AC scans are ignored in the actual dispatcher.

3. `tests/test_sim_bones_with_specialty_reviewers_scenario.py:7`: the scenario explicitly does not spawn specialty reviewer agents; it only pins selection. This leaves the largest Track G Final claim untested at runtime.

4. `jig/sim/assertions.py:15`: the assertion framework still implements only five "bones" assertion kinds while the simulator has grown to ~32 step kinds. Many final-track scenarios can only assert indirect artifact existence/status, not the semantic state each handler claims to exercise.

5. `jig/ticket.py:90`: `created_at`, `updated_at`, and `deferred_at` do not use the shared timezone-aware validator. The schema discipline is applied to `jig/schemas/*`, but the v2-extended `Ticket` model carries key v2 fields and remains outside that validation pattern.

6. `jig/ticket.py:96`: v2 ticket fields (`suite_id`, `module_id`, `capability_ids`, `epic_id`, `layer`, `dev_tier`, `visual_references`) have no kebab-case/literal validation. This weakens schema coherence exactly where PM, reviewers, worktrees, and path helpers meet.

7. `tests/test_schemas_arch.py:194`: several schema tests are round-trip tautologies (`model_dump()` then `model_validate()` then equality). These are useful smoke checks, but they do not prove the schema rejects bad operator/agent-authored YAML or enforces cross-field semantics.

8. `tests/test_reviewers_dispatch.py:379`: per-commit analytics wiring is only simulated by constructing expected `PerCommitCheckFailed`-compatible comments; the dispatcher itself does not emit and this test says the orchestrator hook is separate. That is an integration gap for the reviewer analytics claim.

9. `jig/spec_loader.py:150`: `module_dir(project_root, module_id)` is a raw path join. Most new-module writes are protected indirectly by `ContractsFile(module=...)` validation, but existing-file reads happen before containment checks. This is fragile security posture for MCP-supplied `module_id`.

10. `jig/dev_env/provisioning.py:392`: `operator_supplied` services are skipped completely. The schema/docs describe operator-supplied as a valid strategy, but the real provisioning path does not inject a configured connection string, so agents get no env var for those services.

### Notable observations

1. `jig/orchestrator.py:189`: `_run_agent_with_analytics` is doing analytics, dev provisioning, tier promotion, calibration, and cleanup in one `finally` chain. It mostly composes today, but it is now the integration hotspot and should get a single focused integration test covering all hooks together.

2. `jig/sim/driver.py:400`: the step dispatch table is explicit and still readable, but most handlers stamp ad hoc fields onto `DriverContext`. That makes new assertions easy to bolt on but hard to reason about as a coherent simulator contract.

3. `jig/intent.py:16`: the intent model is structurally consistent but mostly enforces presence/min-length. Its real value depends on reviewers catching boilerplate; without stronger runtime gates it risks becoming ceremony.

4. `jig/reviewers/visual_compliance.py:50` and `jig/reviewers/contract_compliance.py:138`: git-diff helper logic is duplicated across reviewers. Not broken, but divergence here would make reviewer behavior inconsistent.

v2 needs work before shipping: the biggest concerns are path traversal on agent-facing file/path surfaces, real-mode env/provisioning gaps, and reviewer federation selection not actually dispatching the specialty reviewers.
