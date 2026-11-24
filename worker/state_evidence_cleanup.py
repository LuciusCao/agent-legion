"""Bounded startup reclamation for abandoned raw evidence staging files."""

from __future__ import annotations

import os
import stat
import time
from bisect import bisect_right
from pathlib import Path

from worker._atomic import atomic_write
from worker.state_evidence_copies import RAW_EVENTS_FILENAME
from worker.upload.constants import PENDING_FILENAME

RAW_COPY_MIN_AGE_SECONDS = 24 * 60 * 60
STARTUP_SCAN_LIMIT = 256
CURSOR_FILENAME = ".raw-event-cleanup-cursor"


def sweep_raw_event_copies(
    root: Path | None,
    work_root: Path,
    *,
    now: float | None = None,
    min_age_seconds: float = RAW_COPY_MIN_AGE_SECONDS,
    scan_limit: int = STARTUP_SCAN_LIMIT,
) -> int:
    """Delete only old, unowned ``events.jsonl.raw`` regular files.

    Incident directories and processed evidence have no TTL and are never
    removed here. Each sorted pass handles at most ``scan_limit`` direct
    children and atomically persists its last name, so the next startup resumes
    after it (wrapping at the end) instead of starving the suffix behind retained
    incidents. The execution's upload-pending marker is an ownership veto
    regardless of age: restored or currently finalizing attempts may still need
    their raw staging evidence. Fresh files are also kept, covering a dump that
    has not written its incident record yet. Symlinks and foreign filenames are
    left untouched.
    """
    if root is None:
        return 0
    current = time.time() if now is None else now
    removed = 0
    try:
        with os.scandir(root) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        ordered = [entry for entry in ordered if entry.name != CURSOR_FILENAME]
        cursor_path = root / CURSOR_FILENAME
        try:
            after = cursor_path.read_text(encoding="utf-8").strip()
        except OSError:
            after = ""
        names = [entry.name for entry in ordered]
        start = bisect_right(names, after) if after else 0
        batch = [
            ordered[(start + offset) % len(ordered)]
            for offset in range(min(scan_limit, len(ordered)))
        ]
        for entry in batch:
            try:
                if not entry.is_dir(follow_symlinks=False) or "__" not in entry.name:
                    continue
                execution_id = entry.name.split("__", 1)[0]
                if not execution_id or (work_root / execution_id / PENDING_FILENAME).is_file():
                    continue
                raw = Path(entry.path) / RAW_EVENTS_FILENAME
                metadata = raw.stat(follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                if current - metadata.st_mtime < min_age_seconds:
                    continue
                raw.unlink()
                removed += 1
            except FileNotFoundError:
                continue
            except OSError as exc:
                print(f"raw evidence cleanup skipped {entry.name!r}: {exc!r}", flush=True)
        if batch:
            try:
                atomic_write(cursor_path, f"{batch[-1].name}\n", mode=0o600)
            except OSError as exc:
                print(f"raw evidence cleanup cursor not saved: {exc!r}", flush=True)
    except FileNotFoundError:
        return 0
    except OSError as exc:
        print(f"raw evidence cleanup could not scan {root}: {exc!r}", flush=True)
    return removed
