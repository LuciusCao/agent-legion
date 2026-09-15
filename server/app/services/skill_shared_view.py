"""Read model behind the user-facing shared-materials view (issue #643).

The Studio drawer shows a workspace's ``_shared`` materials without
the scoped-token surface: the parsed map, a file listing WITHOUT contents
(the UI fetches single files on demand), and a per-(material, skill) drift
status telling whether each mapped skill repo's HEAD copy still matches the
shared source. The listing covers every readable file under ``_shared``
except ``map.json`` itself (already visualized by the badges) — files
outside references//scripts/ are listed too and grouped by the UI into an
「其他」 section (they can never be map sources, whose contract stays
two-dir). Statuses: ``synced`` (HEAD blob equals the shared bytes),
``pending_sync`` (bytes differ or the shared source is unreadable — the fix
is re-saving the skill, which re-runs the sync), ``missing_in_skill`` (the
repo has no such path at HEAD) and ``skill_not_found`` (no git repo at the
skill's resolved directory).

The skill-name → repo-dir resolution mirrors the save-time sync exactly
(``skill_shared_sync.plan_shared_sync``: second key segment, repo at
``<skills root>/<workspace_id>/<skill_name>``). The HEAD read is
``git show HEAD:<relpath>`` — object-database only, zero working-tree
writes, same hermetic read as EXEC-SKILL-HERMETIC-001. Byte equality is
the comparison: PUT-validated files are UTF-8, so a synced copy is
byte-identical to its source (a non-UTF-8 shared file can only ever read
as ``pending_sync``, which is the honest state — its synced copy carries
replacement characters).

Everything runs under the ``_shared`` edit lock so a concurrent full-state
PUT is never observed half-applied (map generation must match the file
listing and the compared source bytes).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Literal

from server.app.services.job_errors import NotFoundError
from server.app.services.skill_repo import (
    MAX_FILE_BYTES,
    TEXT_EXTENSIONS,
    is_git_repo,
    run_git,
)
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_shared_store import (
    MAP_PATH,
    SHARED_DIR_NAME,
    shared_edit_lock,
)
from server.app.services.skill_shared_sync import SharedMap, load_shared_map
from server.app.skills.skill_roots import skills_root, workspace_skill_dir

DriftStatus = Literal["synced", "pending_sync", "missing_in_skill", "skill_not_found"]


@dataclass(frozen=True)
class SharedFileEntry:
    """One readable shared file (listing form — no content)."""

    path: str
    size: int
    modified_at: str


@dataclass(frozen=True)
class MaterialSkillDrift:
    skill: str
    status: DriftStatus


@dataclass(frozen=True)
class MaterialDrift:
    source: str
    skills: tuple[MaterialSkillDrift, ...]


@dataclass(frozen=True)
class SharedMaterialsView:
    """``map=None`` + empty files is the structured empty state (no ``_shared``)."""

    shared_map: SharedMap | None
    files: tuple[SharedFileEntry, ...]
    drift: tuple[MaterialDrift, ...]


def is_shared_file_path(path: str) -> bool:
    """The read rule for the user-facing view: any relative path inside
    ``_shared`` — including root files and subdirs outside
    references//scripts/ — with no ``..``/absolute/``.git`` components.
    The map ``source`` contract keeps the stricter two-dir rule (owned by
    ``skill_shared_sync.validate_materials``); this only governs what the
    file listing and the on-demand viewer may show."""
    parts = PurePosixPath(path).parts
    return (
        len(parts) >= 1
        and not PurePosixPath(path).is_absolute()
        and ".." not in parts
        and all(part.lower() != ".git" for part in parts)
    )


def _list_file_entries(shared_dir: Path) -> tuple[SharedFileEntry, ...]:
    """All readable files under ``_shared`` except ``map.json`` itself
    (the map is already visualized by the drift badges). Root files and
    files outside references//scripts/ included — the UI groups them into
    an 「其他」 section. Same readability rules as the two-dir walk."""
    entries: list[SharedFileEntry] = []
    for path in sorted(shared_dir.rglob("*")):
        relative = path.relative_to(shared_dir).as_posix()
        if (
            relative == MAP_PATH
            or not is_shared_file_path(relative)
            or not path.is_file()
            or path.is_symlink()
            or path.suffix.lower() not in TEXT_EXTENSIONS
        ):
            continue
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        entries.append(
            SharedFileEntry(
                path=relative,
                size=stat.st_size,
                modified_at=modified,
            )
        )
    return tuple(entries)


def _drift_status(
    workspace_dir: Path, skill: str, source: str, shared_bytes: bytes | None
) -> DriftStatus:
    repo_dir = workspace_dir / skill
    if not is_git_repo(repo_dir):
        return "skill_not_found"
    if shared_bytes is None:
        # The shared source is unreadable; a re-save would fail loudly, so
        # every mapped skill is by definition behind the intended state.
        return "pending_sync"
    result = run_git(repo_dir, ["show", f"HEAD:{source}"], check=False)
    if result.returncode != 0:
        return "missing_in_skill"
    return "synced" if result.stdout == shared_bytes else "pending_sync"


def _compute_drift(
    workspace_dir: Path, shared_dir: Path, shared_map: SharedMap
) -> tuple[MaterialDrift, ...]:
    drift: list[MaterialDrift] = []
    for material in shared_map.materials:
        try:
            shared_bytes = (shared_dir / material.source).read_bytes()
        except OSError:
            shared_bytes = None
        drift.append(
            MaterialDrift(
                source=material.source,
                skills=tuple(
                    MaterialSkillDrift(
                        skill=skill,
                        status=_drift_status(workspace_dir, skill, material.source, shared_bytes),
                    )
                    for skill in material.skills
                ),
            )
        )
    return tuple(drift)


def get_shared_materials_view(
    workspace_id: str, *, base_dir: Path | None = None
) -> SharedMaterialsView:
    """Lock-consistent snapshot: map + content-free file listing + drift."""
    base = base_dir or skills_root()
    workspace_dir = workspace_skill_dir(workspace_id, base_dir=base)
    shared_dir = workspace_dir / SHARED_DIR_NAME
    with shared_edit_lock(shared_dir, base):
        shared_map = load_shared_map(shared_dir)
        if shared_map is None:
            return SharedMaterialsView(shared_map=None, files=(), drift=())
        return SharedMaterialsView(
            shared_map=shared_map,
            files=_list_file_entries(shared_dir),
            drift=_compute_drift(workspace_dir, shared_dir, shared_map),
        )


def read_shared_file_content(
    workspace_id: str, path: str, *, base_dir: Path | None = None
) -> tuple[str, int, bool]:
    """One shared file's text for the on-demand viewer.

    Path shape is validated against the shared-file rule first (422 for
    escapes/``.git`` components); a missing, non-text or symlinked target
    is a 404 — it is not part of the readable surface the listing
    advertises. The target is then RESOLVED and required to stay inside
    the resolved ``_shared`` dir: a symlinked intermediate directory
    (``_shared/docs -> /srv/private``) passes the lexical check and
    final-path symlink checks only inspect the LAST component, so without
    the containment check such a link would read supported-extension files
    anywhere on the host (codex review P1).
    """
    if not is_shared_file_path(path) or PurePosixPath(path).suffix.lower() not in TEXT_EXTENSIONS:
        raise SkillEditValidationError(
            "Invalid shared material path",
            [{"path": path, "error": "path must stay inside _shared (no '..'/absolute/.git)"}],
        )
    base = base_dir or skills_root()
    shared_dir = workspace_skill_dir(workspace_id, base_dir=base) / SHARED_DIR_NAME
    if shared_dir.is_symlink() or shared_dir.parent.is_symlink():
        # codex P1 (#674): _shared itself — or the WORKSPACE dir above it
        # — as a symlink would make the link target the trusted
        # containment root (another workspace's or a host directory) —
        # refuse outright.
        raise NotFoundError("Shared material not found")
    shared_root = shared_dir.resolve()
    target = (shared_root / path).resolve()
    try:
        target.relative_to(shared_root)
    except ValueError as exc:
        raise NotFoundError("Shared material not found") from exc
    if target.is_symlink() or not target.is_file():
        raise NotFoundError("Shared material not found")
    try:
        # One open, fstat + read on the SAME descriptor (codex P2 on
        # #674): size/truncated and content always describe one generation
        # — even when a full-state PUT swaps the directory between the two
        # (taking the shared lock here stays off; the swap is atomic, so
        # the worst case is a transient 404 mid-swap).
        with target.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            raw = handle.read()
    except OSError as exc:
        raise NotFoundError("Shared material not found") from exc
    content = raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    return content, size, size > MAX_FILE_BYTES
