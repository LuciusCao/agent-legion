"""Crash-safe JSON job-artifact writes: fsynced same-directory temp + os.replace.

Split into staging and swap so a caller can stage outside a transaction and
swap inside it after its guard passes (approval decisions, #929); readers
never observe a half-written artifact.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def stage_json(target: Path, payload: dict[str, Any]) -> Path:
    """Write ``payload`` to an fsynced temp file beside ``target`` (same
    directory, so ``os.replace`` onto ``target`` is atomic); a failed write
    leaves no temp file behind. The caller swaps or unlinks the result."""
    descriptor, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    staged = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        # #204 broad-except audit: staging-file cleanup guard, not a
        # swallow — the bare raise re-raises the original verbatim. The
        # width is deliberate: an interrupt during a partial write must
        # also remove the temp file.
        staged.unlink(missing_ok=True)
        raise
    return staged


def write_json_atomic(target: Path, payload: dict[str, Any]) -> None:
    """Stage then swap ``payload`` into ``target`` in one step."""
    staged = stage_json(target, payload)
    try:
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
