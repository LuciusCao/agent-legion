"""Streaming spool for Agent Worker result archives.

The result route streams the request body into a staging file next to its
final home instead of buffering up to max_archive_bytes in the event loop:
at completion waves, one full archive per concurrent report would otherwise
be an in-memory (and GIL) peak. All blocking disk syscalls (mkdir, chunk
writes) run in the threadpool — the route offloads the commit for the same
reason.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import HTTPException, Request
from starlette import concurrency


async def spool_result_body(request: Request, bundle_dir: Path, max_bytes: int) -> Path:
    """Stream the request body to a staging file in ``bundle_dir``.

    Oversize bodies are rejected with 413; the staging file is reclaimed on
    any failure, and the caller publishes it via ``publish_staged_result``.
    Crash-orphaned staging files are reaped by the broker's bundle-dir GC
    (``reaper.reap_terminal_bundles``, age-gated)."""
    await concurrency.run_in_threadpool(bundle_dir.mkdir, parents=True, exist_ok=True)
    descriptor, staging = await concurrency.run_in_threadpool(
        tempfile.mkstemp, dir=bundle_dir, prefix=".result-", suffix=".tmp"
    )
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413, detail="Agent result archive too large")
                await concurrency.run_in_threadpool(handle.write, chunk)
    except BaseException:
        # #204 BaseException audit (batch 5, corrected by review): a staging-
        # file cleanup guard, NOT a swallow — the bare `raise` re-raises the
        # exact original, nothing masked, no type converted. Why the family
        # is BaseException: graceful-shutdown task cancellation (CancelledError),
        # GeneratorExit from an early-closed stream, and post-server-swap
        # cancellation semantics (uvicorn 0.47 does not cancel on client
        # abort — starlette raises ClientDisconnect, an Exception, there —
        # but hypercorn does cancel) all land here; an `except Exception`
        # would unlink for ordinary failures yet SKIP it for exactly those
        # abandonment shapes, leaking the staging file of the uploads most
        # likely to be abandoned. SystemExit/KeyboardInterrupt are not
        # expected on this loop, but passing through with the file already
        # unlinked is still correct: cleanup happens before propagation. The crash-orphaned residue this
        # cannot catch (hard process death between mkstemp and the except)
        # is reaped by the bundle-dir GC noted in the docstring.
        Path(staging).unlink(missing_ok=True)
        raise
    return Path(staging)


def publish_staged_result(staged: Path, archive_path: Path) -> None:
    """Atomically rename the staging file into place (same filesystem)."""
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged, archive_path)


def discard_staged_result(staged: Path) -> None:
    """Reclaim the staging file; a no-op once published."""
    staged.unlink(missing_ok=True)
