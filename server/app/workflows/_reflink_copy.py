"""Copy-on-write private copies (reflink) with a full-copy fallback (#757).

The validation view places declared INPUTS as private copies so a validator
writing or chmod'ing an input can never reach the job dir's upstream
artifact through a shared inode (hardlinks share the inode: in-place writes
AND metadata changes cross the link). On CoW filesystems (APFS, btrfs/xfs)
the private copy is a reflink — zero extra blocks, O(1), keeping the
zero-copy cost discipline; elsewhere it degrades to a full copy. Support is
probed once per filesystem (keyed by the target dir's ``st_dev``) and cached
per process; a failed probe or a single failed clone silently falls back to
the full copy — reflink is a cost optimization and must never introduce a
new failure mode into the completion path. Callers placing into a read view
pass an explicit ``probe_dir`` OUTSIDE the view (its parent — same
filesystem, verdict unchanged): the probe's cleanup unlink is deliberately
suppressed, and a failed one must never leave ``.reflink-probe-*`` residue
where the validator's rglob can see it (#876 B 员 P3).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
from contextlib import suppress
from pathlib import Path

_FICLONE = 0x40049409  # Linux ioctl: clone the src fd's bytes into the dst fd

# Per-process probe cache: target filesystem's st_dev -> reflink supported.
_support: dict[int, bool] = {}
_support_lock = threading.Lock()


def copy_private(source: Path, spot: Path, *, probe_dir: Path | None = None) -> None:
    """Copy ``source`` to ``spot`` as a private inode (reflink when possible).

    Raises whatever the full copy raises (FileNotFoundError when the source
    vanished mid-placement, other OSError on disk/permission failures) — the
    caller's failure grading decides; the reflink half never raises.

    ``probe_dir``: the directory the one-per-filesystem reflink probe writes
    its temp file in. Callers placing INTO a read view (the validation view)
    pass a directory OUTSIDE the view (its parent — same filesystem by
    construction, so the probe verdict stays valid) because the probe's
    cleanup unlink is deliberately suppressed: a failed one would otherwise
    leave ``.reflink-probe-*`` residue INSIDE the view, visible to the
    validator's rglob (#876 B 员 P3). None keeps the legacy spot.parent
    probe (direct callers/tests).
    """
    if not _try_reflink(source, spot, probe_dir if probe_dir is not None else spot.parent):
        shutil.copy2(source, spot)


def _try_reflink(source: Path, spot: Path, probe_dir: Path) -> bool:
    """Best-effort CoW clone; False = caller falls back to a full copy."""
    try:
        if not _supported(probe_dir.stat().st_dev, probe_dir):
            return False
        return _clone(source, spot)
    except OSError:
        # A single file's clone failing on a "supported" filesystem (a
        # cross-device edge, a per-file limit) falls back per file and does
        # not poison the cached verdict.
        return False


def _supported(dev: int, probe_dir: Path) -> bool:
    """Probe once per filesystem, cache per process (thread-safe)."""
    with _support_lock:
        cached = _support.get(dev)
        if cached is not None:
            return cached
        supported = _probe(probe_dir)
        _support[dev] = supported
        return supported


def _probe(probe_dir: Path) -> bool:
    """Try cloning a small temp file inside the target dir; never raises."""
    fd, src = tempfile.mkstemp(dir=probe_dir, prefix=".reflink-probe-")
    dst = f"{src}.dst"
    try:
        os.write(fd, b"\0")
        return _clone(Path(src), Path(dst))
    except OSError:
        return False
    finally:
        os.close(fd)
        with suppress(OSError):
            os.unlink(src)
        with suppress(OSError):
            os.unlink(dst)


if sys.platform == "darwin":
    import ctypes

    # clonefile(2) exists since macOS 10.12; a libSystem without it must
    # degrade to the copy path, never fail validations (AttributeError from
    # the symbol lookup is not an OSError and would escape the fallback).
    _clonefile = getattr(ctypes.CDLL(None, use_errno=True), "clonefile", None)

    def _clone(source: Path, spot: Path) -> bool:
        """macOS APFS: clonefile(2) from libSystem. Raises OSError on failure."""
        if _clonefile is None:
            return False
        # clonefile(src, dst, flags=0) → 0 or -1/errno; dst must not exist.
        if _clonefile(os.fsencode(source), os.fsencode(spot), 0):
            raise OSError(ctypes.get_errno(), "clonefile failed")
        return True

else:

    def _clone(source: Path, spot: Path) -> bool:
        """Linux: FICLONE ioctl; unsupported filesystems raise OSError."""
        import fcntl

        src_fd = os.open(source, os.O_RDONLY)
        try:
            # O_EXCL: the caller unlinked any previous entry; FICLONE fills
            # the fresh fd. A failed ioctl may leave the empty dst behind —
            # the copy fallback overwrites it in place.
            dst_fd = os.open(spot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            try:
                fcntl.ioctl(dst_fd, _FICLONE, src_fd)
            finally:
                os.close(dst_fd)
        finally:
            os.close(src_fd)
        return True
