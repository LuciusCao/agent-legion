"""Per-file mechanics of the declared validation view (#757).

Split from ``validation_view`` for the file-size budget — that module owns
the view contract and the construction/exit orchestration; this one owns
the filesystem mechanics: safe-relative filtering, hardlink-or-copy
placement with its failure grading, the placement records the exit arms
work against, the output reconcile back into the run view, and the inputs
read-only enforcement. The input byte-source decision (dispatch-frozen CAS
first, job-dir fallback, #828/#830/#833) lives in the sibling
``_validation_view_inputs``. See ``validation_view``'s docstring for the
semantics; tests/workflows/test_output_validation_view.py pins them.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from server.app.workflows._reflink_copy import copy_private


@dataclass(frozen=True)
class InputSnapshot:
    """An input's identity at placement; the exit check compares against it."""

    rel: str
    ino: int
    dev: int
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class OutputPlacement:
    """An output's view entry at placement: inode identity + channel."""

    rel: str
    ino: int
    dev: int
    linked: bool  # hardlinked (in-place writes propagate) vs copied


@dataclass(frozen=True)
class ViewPlacements:
    """What materialize_validation_view placed, for the exit-time arms."""

    inputs: tuple[InputSnapshot, ...]
    outputs: tuple[OutputPlacement, ...]


def safe_relative(name: str) -> str | None:
    """The declared name as a view-relative path; None = unsafe (abs/``..``).

    ``PurePosixPath`` collapses ``./`` and ``//``, so a non-canonical
    spelling (``./out.json``) compares equal to the canonical name in the
    input/output overlap exclusion (#833 codex P2).
    """
    rel = PurePosixPath(name)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    return rel.as_posix()


def place(
    rel: str, source: Path, target: Path, *, private: bool
) -> tuple[os.stat_result, bool] | None:
    """Place ``source`` into the view at ``rel``; returns (view stat, linked).

    ``private=True`` (declared inputs): a reflink-or-copy private inode
    (#757 P1 — a hardlink shares the inode with the source, so a validator's
    in-place write or chmod would cross over into the job dir's upstream
    artifact or the shared CAS blob, #833).
    ``private=False`` (declared outputs): a hardlink, so the validator's
    in-place cleaning propagates to the bytes the finish gate promotes (the
    replace family is covered by the reconcile arm).

    ``None`` = the source is absent (or vanished mid-placement): the
    missing-source family stays fail-open and the validator judges absence.
    Placement failures (mkdir prefix collisions, disk/permission errors on
    the copy fallback) raise — an unbuildable view fails closed.
    """
    try:
        if not stat.S_ISREG(source.stat().st_mode):
            return None
    except FileNotFoundError:
        return None
    spot = target / rel
    spot.parent.mkdir(parents=True, exist_ok=True)
    if spot.is_symlink() or spot.is_file():
        spot.unlink()
    if private:
        try:
            copy_private(source, spot)
        except FileNotFoundError:
            return None
        return os.stat(spot), False
    try:
        os.link(source, spot)
        return os.stat(spot), True
    except FileNotFoundError:
        return None
    except OSError:
        pass  # hardlink-unsupported mount: fall through to the copy
    try:
        shutil.copy2(source, spot)
    except FileNotFoundError:
        return None
    return os.stat(spot), False


def reconcile_outputs(
    view: Path, outputs: tuple[OutputPlacement, ...], output_source: Path
) -> None:
    """Sync the validator's output mutations back into the run view.

    In-place writes never need syncing (the hardlink shares the inode). A
    replaced entry (temp + os.replace / delete-and-rewrite, or a copied
    fallback entry whose mutations cannot propagate) is synced with a
    temp-write + atomic os.replace into ``output_source`` — a sync failure
    raises so the run fails closed rather than promoting uncleaned bytes.
    A validator-deleted (or non-file-replaced) output propagates the
    deletion: the finish gate's missing-source containment then fails the
    run, exactly as when validators ran inside the staging view.
    """
    for placed in outputs:
        spot = view / placed.rel
        source = output_source / placed.rel
        if spot.is_file():
            st = spot.stat()
            if placed.linked and (st.st_ino, st.st_dev) == (placed.ino, placed.dev):
                continue
            _sync_back(spot, source)
        elif not source.is_file() and not source.is_symlink():
            continue
        else:
            source.unlink()


def verify_inputs_untouched(view: Path, inputs: tuple[InputSnapshot, ...]) -> None:
    """Enforce the inputs read-only contract; raises on any mutation."""
    for snap in inputs:
        spot = view / snap.rel
        try:
            st = spot.stat()
        except FileNotFoundError:
            st = None
        if (
            st is None
            or (st.st_ino, st.st_dev) != (snap.ino, snap.dev)
            or st.st_mtime_ns != snap.mtime_ns
            or st.st_size != snap.size
        ):
            raise ValueError(
                f"validator mutated declared input {snap.rel!r} (inputs are read-only)"
            )


def _sync_back(view_file: Path, source: Path) -> None:
    """Replace ``source`` with ``view_file``'s bytes via temp + os.replace.

    Atomic (the run view never holds a partial file) and inode-fresh: a
    run-view entry hardlinked elsewhere (a remote-ref promoted name also
    linked into the job dir) keeps its old bytes there.
    """
    fd, tmp = tempfile.mkstemp(dir=source.parent, prefix=f".reconcile-{source.name}-")
    os.close(fd)
    try:
        shutil.copy2(view_file, tmp)
        os.replace(tmp, source)
    except BaseException:
        # #204 broad-except audit: temp-file cleanup, not a swallow — the
        # copy/replace surface (OSError: disk full, vanished parent) must
        # propagate so the run fails closed instead of promoting uncleaned
        # bytes; the arm only re-raises after unlinking the partial temp so
        # no scratch survives in the run view. BaseException so even
        # KeyboardInterrupt leaves no half-written temp behind.
        with suppress(OSError):
            os.unlink(tmp)
        raise
