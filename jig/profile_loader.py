"""Profile loader + applier + template-copy helpers.

A project profile bundles SA depth and per-size workflow routing
into a named config. Two built-in profiles ship under
``jig/defaults/profiles/`` (``small.yaml``, ``medium.yaml``).
Project-local overrides land in ``.jig/profiles/<name>.yaml``.

Selection flow:

1. ``load_profile(name, project_path)`` resolves the named profile,
   preferring project-local copies.
2. ``apply_profile(config, profile)`` writes the profile's choices
   into the in-memory ``Config`` — ``sa_role`` lands on
   ``config.profile``, the workflow routing merges into
   ``config.workflows.default_by_size``, ``available`` constrains
   ``config.workflows.available``.
3. ``copy_profile_templates(profile, project_path)`` writes the
   profile YAML + all referenced workflow YAMLs into ``.jig/`` so
   the project is self-contained going forward.

The caller (``cli.start`` or PM ``needs_info`` handler) is responsible
for invoking ``save_config`` after ``apply_profile`` to persist.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from jig.config import Config
from jig.persistence import _defaults_dir, _jig_dir
from jig.schemas.profile import Profile


def _profile_path_project(project_path: Path, name: str) -> Path:
    return _jig_dir(project_path) / "profiles" / f"{name}.yaml"


def _profile_path_shipped(name: str) -> Path:
    return _defaults_dir() / "profiles" / f"{name}.yaml"


def load_profile(name: str, project_path: Path | None = None) -> Profile:
    """Load a profile YAML by name.

    Resolution order matches ``load_role`` / ``load_workflow``:

    1. ``.jig/profiles/<name>.yaml`` (project-local override) when
       ``project_path`` is provided.
    2. ``jig/defaults/profiles/<name>.yaml`` (shipped).

    Raises ``FileNotFoundError`` if neither exists.
    """
    if project_path is not None:
        local = _profile_path_project(project_path, name)
        if local.is_file():
            data = yaml.safe_load(local.read_text())
            return Profile.model_validate(data)
    shipped = _profile_path_shipped(name)
    if shipped.is_file():
        data = yaml.safe_load(shipped.read_text())
        return Profile.model_validate(data)
    raise FileNotFoundError(
        f"profile {name!r} not found. "
        f"Looked in .jig/profiles/{name}.yaml and "
        f"jig/defaults/profiles/{name}.yaml."
    )


def list_profiles(project_path: Path | None = None) -> list[Profile]:
    """Return every profile visible to this project.

    Project-local profiles win when the same name appears in both
    layers. The list is deterministic — sorted by name.
    """
    seen: dict[str, Profile] = {}
    if project_path is not None:
        local_dir = _jig_dir(project_path) / "profiles"
        if local_dir.is_dir():
            for f in sorted(local_dir.glob("*.yaml")):
                data = yaml.safe_load(f.read_text())
                p = Profile.model_validate(data)
                seen[p.name] = p
    shipped_dir = _defaults_dir() / "profiles"
    if shipped_dir.is_dir():
        for f in sorted(shipped_dir.glob("*.yaml")):
            data = yaml.safe_load(f.read_text())
            p = Profile.model_validate(data)
            if p.name not in seen:
                seen[p.name] = p
    return [seen[k] for k in sorted(seen)]


def apply_profile(config: Config, profile: Profile) -> Config:
    """Return a new Config with the profile's choices merged in.

    Three places get updated:

    * ``config.profile.name`` / ``config.profile.sa_role`` — recorded
      as the source of truth.
    * ``config.workflows.default_by_size`` — the profile's routing
      table is merged on top (profile wins on overlapping keys).
    * ``config.workflows.available`` — replaced with the profile's
      allowlist if non-empty (empty means no restriction).

    Pydantic models are immutable in the sense that we don't mutate
    the input; we deep-copy and patch.
    """
    data = config.model_dump(mode="python", by_alias=True)
    data.setdefault("profile", {})
    data["profile"]["name"] = profile.name
    data["profile"]["sa_role"] = profile.sa_role

    workflows = data.setdefault("workflows", {})
    merged_by_size = dict(workflows.get("default_by_size") or {})
    merged_by_size.update(profile.workflows.default_by_size)
    workflows["default_by_size"] = merged_by_size
    if profile.workflows.available:
        workflows["available"] = list(profile.workflows.available)

    return Config.model_validate(data)


def copy_profile_templates(profile: Profile, project_path: Path) -> None:
    """Copy the profile YAML and all referenced workflow YAMLs into ``.jig/``.

    After this runs the project is self-contained: the orchestrator
    + init flow read from ``.jig/profiles/`` and ``.jig/workflows/``
    rather than continuing to fall back on the shipped defaults.

    Idempotent: existing destination files are left alone so operator
    customisations win. Missing shipped sources are skipped silently
    (the profile may reference workflows that don't ship — operator
    can supply locally).
    """
    profiles_dir = _jig_dir(project_path) / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    dest_profile = profiles_dir / f"{profile.name}.yaml"
    if not dest_profile.is_file():
        src_profile = _profile_path_shipped(profile.name)
        if src_profile.is_file():
            shutil.copyfile(src_profile, dest_profile)

    workflows_dir = _jig_dir(project_path) / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    needed: set[str] = set(profile.workflows.default_by_size.values())
    needed.update(profile.workflows.available)
    for wf_name in sorted(needed):
        dest = workflows_dir / f"{wf_name}.yaml"
        if dest.is_file():
            continue
        src = _defaults_dir() / "workflows" / f"{wf_name}.yaml"
        if src.is_file():
            shutil.copyfile(src, dest)

    # Check catalog: also copy the shipped catalog into ``.jig/`` so
    # operators have an editable starting point alongside the profile
    # and workflow YAMLs. Skipped if the operator has already
    # authored ``.jig/checks.yaml`` (their edits win).
    dest_checks = _jig_dir(project_path) / "checks.yaml"
    if not dest_checks.is_file():
        src_checks = _defaults_dir() / "checks.yaml"
        if src_checks.is_file():
            shutil.copyfile(src_checks, dest_checks)
