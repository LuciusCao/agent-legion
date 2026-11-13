"""Bounded startup reclamation for abandoned raw evidence staging files."""

from __future__ import annotations

import itertools
import os
import stat
import time
from pathlib import Path

from worker.upload.constants import PENDING_FILENAME

RAW_EVENTS_FILENAME = "events.jsonl.raw"
RAW_COPY_MIN_AGE_SECONDS = 24 * 60 * 60
STARTUP_SCAN_LIMIT = 256


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
    removed here. A direct-child scan is capped per startup. The execution's
    upload-pending marker is an ownership veto regardless of age: restored or
    currently finalizing attempts may still need their raw staging evidence.
    Fresh files are also kept, covering a dump that has not written its
    incident record yet. Symlinks and foreign filenames are left untouched.
    """
    if root is None:
        return 0
    current = time.time() if now is None else now
    removed = 0
    try:
        with os.scandir(root) as entries:
            for entry in itertools.islice(entries, scan_limit):
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
                    print(
                        f"raw evidence cleanup skipped {entry.name!r}: {exc!r}",
                        flush=True,
                    )
    except FileNotFoundError:
        return 0
    except OSError as exc:
        print(f"raw evidence cleanup could not scan {root}: {exc!r}", flush=True)
    return removed
