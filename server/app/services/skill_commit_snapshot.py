"""Bounded editing view of one immutable Git tree, independent of host files.

The repository is not a save batch. Export every editable committed file
within the snapshot budget; callers choose at most 100 writes for one save.
Preflight modes, paths and declared blob sizes before loading any content.
"""

from pathlib import Path
from typing import Any

from server.app.services import skill_repo
from server.app.services.skill_edit_checks import target_path_errors
from server.app.services.skill_edit_snapshot import edit_file
from server.app.services.skill_repo_edit import SkillEditValidationError

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
ENTRY_OVERHEAD_BYTES = 128


def commit_snapshot(repo: Path, commit: str) -> list[dict[str, Any]]:
    listing = skill_repo.run_git(repo, ["ls-tree", "-r", "-l", "-z", commit]).stdout
    entries: list[tuple[str, str]] = []
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
            if mode not in (b"100644", b"100755") or kind != b"blob":
                raise ValueError("snapshot requires regular Git blobs")
            if len(path) > 512 or target_path_errors([path]):
                raise ValueError("snapshot contains an unwritable path")
            size = int(raw_size)
            if not 0 <= size <= skill_repo.MAX_FILE_BYTES:
                raise ValueError("file exceeds the editable byte limit")
            total += size + len(raw_path) + ENTRY_OVERHEAD_BYTES
            if total > MAX_SNAPSHOT_BYTES:
                raise ValueError("Git tree exceeds snapshot byte budget")
            entries.append((path, oid.decode("ascii")))
    except (UnicodeError, ValueError) as exc:
        raise SkillEditValidationError(
            "Cannot export a complete Git editing snapshot",
            [{"path": ".", "error": str(exc)}],
        ) from exc
    files = [
        edit_file(path, skill_repo.run_git(repo, ["cat-file", "blob", oid]).stdout)
        for path, oid in entries
    ]
    return sorted(
        files, key=lambda item: (item["path"] not in ("SKILL.md", "contract.yaml"), item["path"])
    )
