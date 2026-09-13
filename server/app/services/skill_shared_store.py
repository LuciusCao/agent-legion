"""Directory-level IO for the workspace ``_shared`` materials (issue #633).

The store primitives behind the shared-materials surface: locating the
workspace dir, reading files under the skill-file read rules, the
cross-process ``_shared`` edit lock, and the all-or-nothing full-state
write. The map.json CONTRACT and the save-time sync PLANNING live in
``skill_shared_sync`` (which imports this module — keep the dependency
one-way).

Locking (codex review R2 P1/P2): the full-state PUT and every reader that
must see a consistent snapshot (``plan_shared_sync`` inside the
``save_version`` lock, the GET endpoint) serialize on
``edit_lock_for(<ws>/_shared)`` — lock name ``<ws>--_shared.lock``. The
PUT stages the ENTIRE new tree in a sibling temp dir first, then swaps it
in with two renames under the lock: readers never observe a half-applied
state (old-or-new, never mixed), a mid-write failure leaves the old dir
untouched, and the swap IS the removal pass — files dropped from the
payload disappear with the replaced dir (full-state semantics).
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Sequence
from pathlib import Path

from server.app.services.job_errors import JobServiceError
from server.app.services.skill_repo import MAX_FILE_BYTES, TEXT_EXTENSIONS
from server.app.services.skill_repo_edit import edit_lock_for
from server.app.skills.skill_roots import workspace_skill_dir

SHARED_DIR_NAME = "_shared"
MAP_PATH = "map.json"


class SharedMaterialWriteError(JobServiceError):
    """Operational IO failure while staging/swapping ``_shared`` — raised
    only AFTER validation passed, so routes surface it as an unmapped
    5xx (the SkillGitError convention), never a client 422."""


def shared_dir_for(base_dir: Path, skill_key: str) -> Path:
    """The workspace ``_shared`` dir behind a skill key (first segment =
    workspace id by skills-root convention); the key shape itself is
    validated by the caller's own ``_skill_dir`` guard."""
    workspace_id = skill_key.split("/", 1)[0]
    return workspace_skill_dir(workspace_id, base_dir=base_dir) / SHARED_DIR_NAME


def shared_edit_lock(shared_dir: Path, base_dir: Path):
    """Cross-process lock serializing ``_shared`` writers and snapshot
    readers (``save_version``'s sync read acquires it NESTED inside the
    skill repo lock; lock order is always skill → shared, and the PUT
    takes only the shared lock, so no cycle exists)."""
    return edit_lock_for(shared_dir, base_dir, None)


def read_shared_text(path: Path) -> str:
    """Read one shared file under the skill-file read rules (text
    extensions only, no symlinks, 128 KB cap, UTF-8 with replacement) —
    the same contract as the catalog read, so ``_shared`` files and skill
    repo files cannot drift apart in what the surface accepts."""
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise OSError(f"unsupported file extension: {path.name!r}")
    if path.is_symlink() or not path.is_file():
        raise OSError(f"not a regular file: {path.name!r}")
    return path.read_bytes()[:MAX_FILE_BYTES].decode("utf-8", errors="replace")


def read_shared_files(shared_dir: Path, material_dirs: tuple[str, ...]) -> list[dict]:
    """Readable shared files (same shape as the skill detail read)."""
    files: list[dict] = []
    for folder_name in material_dirs:
        folder = shared_dir / folder_name
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if (
                not path.is_file()
                or path.is_symlink()
                or path.suffix.lower() not in TEXT_EXTENSIONS
            ):
                continue
            size = path.stat().st_size
            raw = path.read_bytes()[:MAX_FILE_BYTES]
            files.append(
                {
                    "path": path.relative_to(shared_dir).as_posix(),
                    "size": size,
                    "content": raw.decode("utf-8", errors="replace"),
                    "truncated": size > MAX_FILE_BYTES,
                }
            )
    return files


def write_shared_materials(
    shared_dir: Path, files: Sequence[tuple[str, str]], base_dir: Path
) -> None:
    """Apply the full-state write atomically: stage every file (relative
    posix path + content) in a sibling temp dir — any failure there
    leaves the live dir untouched — then swap under the shared lock with
    two same-filesystem renames; the replaced dir's retirement is the
    removal pass for dropped files."""
    staging = shared_dir.parent / f"{SHARED_DIR_NAME}.tmp-{uuid.uuid4().hex[:12]}"
    retired = shared_dir.parent / f"{SHARED_DIR_NAME}.old-{uuid.uuid4().hex[:12]}"
    try:
        for relative, content in files:
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        with shared_edit_lock(shared_dir, base_dir):
            had_previous = shared_dir.is_dir()
            if had_previous:
                os.rename(shared_dir, retired)
            try:
                os.rename(staging, shared_dir)
            except OSError:
                if had_previous:
                    os.rename(retired, shared_dir)  # restore, staging stays garbage
                raise
    except OSError as exc:
        raise SharedMaterialWriteError(f"shared materials write failed: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)
