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


def read_shared_text(path: Path, *, strict: bool = False) -> str:
    """Read one shared file under the skill-file read rules (text
    extensions only, no symlinks, 128 KB cap, UTF-8 with replacement) —
    the same contract as the catalog read, so ``_shared`` files and skill
    repo files cannot drift apart in what the surface accepts.

    ``strict=True`` (the sync path, codex R3 P1): a file whose raw size
    exceeds the cap RAISES instead of returning truncated text — the sync
    copies the shared file authoritatively into every mapped skill, so a
    display-style truncation (possibly mid UTF-8 sequence, rendered as
    replacement characters) must never ride a save_skill_version commit
    while still reporting success. The PUT boundary already rejects
    oversized payloads; this guards files that arrived through another
    path."""
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise OSError(f"unsupported file extension: {path.name!r}")
    if path.is_symlink() or not path.is_file():
        raise OSError(f"not a regular file: {path.name!r}")
    raw = path.read_bytes()
    if strict and len(raw) > MAX_FILE_BYTES:
        raise OSError(
            f"shared material exceeds the {MAX_FILE_BYTES}-byte cap "
            f"({len(raw)} bytes); refusing to sync a truncated copy"
        )
    return raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace")


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
