"""Workspace-level shared skill materials (issue #633).

A workspace's skills often need to share one set of reference materials
while staying independently edited git repos. The opt-in layout under the
workspace skill dir (``<skills root>/<workspace_id>/``) is::

    _shared/map.json        # {"version": 1, "materials": [{source, skills}]}
    _shared/references/…    # shared reference files
    _shared/scripts/…       # shared script files

``_shared`` is deliberately NOT a git repo in v1: the skills' commits
record the synced copies (same relative path, shared source authoritative)
— that IS the audit trail, so a second history would duplicate it.

This module owns the map.json contract and the save-time sync planning:
``plan_shared_sync`` loads the map, selects the materials mapped to one
skill, reads their contents from ``_shared`` and reports conflicts with
the caller-supplied file list. It runs inside the ``save_version`` lock
BEFORE any file is written, so every failure mode (malformed map, missing
source, conflicting hand-supplied path) is a pre-write 422
(``SkillEditValidationError``, routed by the save endpoint) — the
existing path-safety, untracked-overwrite refusal, contract re-check,
commit and tag then see the injected files like agent-authored ones.
Workspaces without ``_shared`` get a no-op plan (zero behavior change).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from server.app.services.skill_repo import MAX_FILE_BYTES, TEXT_EXTENSIONS
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.skills.skill_roots import workspace_skill_dir

SHARED_DIR_NAME = "_shared"
MAP_PATH = "map.json"
# Skill names are the second key segment — same dir-name shape the skills
# root enforces for workspace ids (skill_roots._WORKSPACE_ID_RE).
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MATERIAL_DIRS = ("references", "scripts")


@dataclass(frozen=True)
class SharedMaterial:
    """One validated map entry."""

    source: str
    skills: tuple[str, ...]


@dataclass(frozen=True)
class SharedMap:
    """A loaded, validated ``_shared/map.json``."""

    shared_dir: Path
    materials: tuple[SharedMaterial, ...]


@dataclass(frozen=True)
class SharedSyncPlan:
    """Pre-write sync plan for one ``save_version`` call."""

    files: tuple[tuple[str, str], ...] = ()  # (relative path, content) to inject


def shared_dir_for(base_dir: Path, skill_key: str) -> Path:
    """The workspace ``_shared`` dir behind a skill key (first segment =
    workspace id by skills-root convention); the key shape itself is
    validated by the caller's own ``_skill_dir`` guard."""
    workspace_id = skill_key.split("/", 1)[0]
    return workspace_skill_dir(workspace_id, base_dir=base_dir) / SHARED_DIR_NAME


def _read_shared_text(path: Path) -> str:
    """Read one shared file under the skill-file read rules (text
    extensions only, no symlinks, 128 KB cap, UTF-8 with replacement) —
    the same contract as the catalog read, so ``_shared`` files and skill
    repo files cannot drift apart in what the surface accepts."""
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise OSError(f"unsupported file extension: {path.name!r}")
    if path.is_symlink() or not path.is_file():
        raise OSError(f"not a regular file: {path.name!r}")
    return path.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", errors="replace")


def _invalid(errors: list[dict[str, str]]) -> SkillEditValidationError:
    return SkillEditValidationError("Invalid shared materials map", errors)


def validate_materials(raw: object) -> tuple[SharedMaterial, ...]:
    """Validate the parsed map.json ``materials`` list.

    Every rule is checked before any caller writes anything: a material
    ``source`` must be a relative path under ``references/`` or
    ``scripts/`` (no ``..``/absolute/``.git`` components, any case) and
    ``skills`` a non-empty list of dir-name-shaped skill names. Sources
    must be unique — one shared file maps to one authoritative copy, and
    duplicate entries would fight over whose content wins. An EMPTY
    materials list is valid: the workspace opted into ``_shared`` but
    maps nothing yet.
    """
    if not isinstance(raw, list):
        raise _invalid([{"path": MAP_PATH, "error": "materials must be a list"}])
    materials: list[SharedMaterial] = []
    seen_sources: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) - {"source", "skills"}:
            raise _invalid([{"path": MAP_PATH, "error": "each material must be {source, skills}"}])
        source = entry.get("source")
        skills = entry.get("skills")
        if not isinstance(source, str) or not source:
            raise _invalid([{"path": MAP_PATH, "error": "source must be a non-empty string"}])
        parts = PurePosixPath(source).parts
        if (
            len(parts) < 2
            or parts[0] not in _MATERIAL_DIRS
            or PurePosixPath(source).is_absolute()
            or ".." in parts
            or any(part.lower() == ".git" for part in parts)
        ):
            raise _invalid(
                [
                    {
                        "path": MAP_PATH,
                        "error": f"source {source!r} must stay under references/ or "
                        "scripts/ with no '..'/absolute/.git components",
                    }
                ]
            )
        if (
            not isinstance(skills, list)
            or not skills
            or not all(isinstance(name, str) and _SKILL_NAME_RE.fullmatch(name) for name in skills)
        ):
            raise _invalid(
                [
                    {
                        "path": MAP_PATH,
                        "error": f"skills for {source!r} must be a non-empty list of "
                        "skill names (second key segment, ^[a-z0-9][a-z0-9_-]{0,63}$)",
                    }
                ]
            )
        if source in seen_sources:
            raise _invalid([{"path": MAP_PATH, "error": f"duplicate source {source!r}"}])
        seen_sources.add(source)
        materials.append(SharedMaterial(source=source, skills=tuple(skills)))
    return tuple(materials)


def load_shared_map(shared_dir: Path) -> SharedMap | None:
    """Load and validate ``_shared/map.json``; None when ``_shared`` is absent.

    A present-but-broken map is an error, never a silent skip: the
    workspace opted into sharing, so a malformed map must fail the save
    loudly instead of half-syncing.
    """
    if not shared_dir.is_dir():
        return None
    map_path = shared_dir / MAP_PATH
    if not map_path.is_file():
        raise _invalid([{"path": MAP_PATH, "error": f"{MAP_PATH} is missing"}])
    try:
        raw = json.loads(_read_shared_text(map_path))
    except (OSError, UnicodeDecodeError) as exc:
        raise _invalid([{"path": MAP_PATH, "error": f"unreadable: {exc}"}]) from exc
    except json.JSONDecodeError as exc:
        raise _invalid([{"path": MAP_PATH, "error": f"malformed JSON: {exc}"}]) from exc
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise _invalid([{"path": MAP_PATH, "error": "version must be 1"}])
    return SharedMap(shared_dir=shared_dir, materials=validate_materials(raw.get("materials")))


def plan_shared_sync(
    base_dir: Path, skill_key: str, files: Sequence[tuple[str, str]]
) -> SharedSyncPlan:
    """Plan the save-time sync for one skill (all validation, no writes).

    Returns the (path, content) pairs to inject into the write set; raises
    ``SkillEditValidationError`` for a malformed map, a missing/unreadable
    mapped source, or a caller-supplied file colliding with a mapped
    shared path — the shared copy is authoritative for mapped paths, so a
    hand-supplied stale copy must not silently shadow a newer shared
    revision (the agent removes those paths and re-saves).
    """
    shared_map = load_shared_map(shared_dir_for(base_dir, skill_key))
    if shared_map is None:
        return SharedSyncPlan()
    skill_name = skill_key.split("/", 1)[1]
    mapped = [m for m in shared_map.materials if skill_name in m.skills]
    if not mapped:
        return SharedSyncPlan()
    supplied = {path for path, _ in files}
    conflicts = sorted(supplied & {m.source for m in mapped})
    if conflicts:
        raise _invalid(
            [
                {
                    "path": path,
                    "error": "path is a mapped shared material (the shared copy wins); "
                    "remove it from the save payload",
                }
                for path in conflicts
            ]
        )
    errors: list[dict[str, str]] = []
    injected: list[tuple[str, str]] = []
    for material in mapped:
        try:
            content = _read_shared_text(shared_map.shared_dir / material.source)
        except (OSError, UnicodeDecodeError) as exc:
            errors.append({"path": material.source, "error": f"shared source unreadable: {exc}"})
            continue
        injected.append((material.source, content))
    if errors:
        raise _invalid(errors)
    return SharedSyncPlan(files=tuple(injected))
