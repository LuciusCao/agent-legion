"""Bounded editing view of one immutable Git tree, independent of host files.

The repository is not a save batch. Export every editable committed file
within the snapshot budget; callers choose at most 100 writes for one save.
Preflight modes, paths and declared blob sizes before loading any content.
"""

from pathlib import Path
from typing import Any

from server.app.services.skill_build_residue import is_build_residue
from server.app.services.skill_edit_checks import target_path_errors
from server.app.services.skill_edit_snapshot import edit_file
from server.app.services.skill_repo_edit import SkillEditValidationError
from server.app.services.skill_snapshot_git import SnapshotGit, batch_blobs
from server.app.skill_authoring_limits import SKILL_CONTENT_MAX_CHARS, SKILL_CONTENT_MAX_UTF8_BYTES

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
ENTRY_OVERHEAD_BYTES = 128


def commit_snapshot(
    repo: Path, commit: str, reader: SnapshotGit | None = None
) -> list[dict[str, Any]]:
    reader = reader or SnapshotGit(repo)
    listing = reader.run(["ls-tree", "-r", "-l", "-z", commit], MAX_SNAPSHOT_BYTES)
    entries: list[tuple[str, str, int]] = []
    total = 0
    try:
        if len(listing) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Git tree metadata exceeds snapshot budget")
        for entry in listing.split(b"\0"):
            if not entry:
                continue
            meta, raw_path = entry.split(b"\t", 1)
            mode, kind, oid, raw_size = meta.split()
            path = raw_path.decode("utf-8")
            if is_build_residue(path):
                # #1038: committed bytecode cache (repos born without a
                # .gitignore) is not editable content; skipping it keeps the
                # snapshot exportable. Skill saves write only the declared
                # files, so this omission never implies a deletion.
                continue
            if mode not in (b"100644", b"100755") or kind != b"blob":
                raise ValueError("snapshot requires regular Git blobs")
            if len(path) > 512 or target_path_errors([path]):
                raise ValueError("snapshot contains an unwritable path")
            size = int(raw_size)
            if not 0 <= size <= SKILL_CONTENT_MAX_UTF8_BYTES:
                raise ValueError("file exceeds the editable byte limit")
            total += size + len(raw_path) + ENTRY_OVERHEAD_BYTES
            if total > MAX_SNAPSHOT_BYTES:
                raise ValueError("Git tree exceeds snapshot byte budget")
            entries.append((path, oid.decode("ascii"), size))
        blobs = batch_blobs(reader, entries)
    except (UnicodeError, ValueError) as exc:
        raise SkillEditValidationError(
            "Cannot export a complete Git editing snapshot",
            [{"path": ".", "error": str(exc)}],
        ) from exc
    files = []
    for (path, _, _), raw in zip(entries, blobs, strict=True):
        item = edit_file(
            path,
            raw,
            max_bytes=SKILL_CONTENT_MAX_UTF8_BYTES,
        )
        if len(item["content"]) > SKILL_CONTENT_MAX_CHARS:
            raise SkillEditValidationError(
                "Cannot export a writable Git editing snapshot",
                [{"path": path, "error": "file exceeds the editable character limit"}],
            )
        files.append(item)
    return sorted(
        files, key=lambda item: (item["path"] not in ("SKILL.md", "contract.yaml"), item["path"])
    )
