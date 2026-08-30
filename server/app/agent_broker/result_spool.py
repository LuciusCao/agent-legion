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
        # #204 BaseException audit (batch 5): deliberate staging-file cleanup
        # guard, NOT a swallow — the bare `raise` below re-raises the exact
        # original, so nothing is masked and no type is converted. The reason
        # it must be BaseException and not Exception: this is an async route
        # handler on the event loop, and Starlette/FastAPI task cancellation
        # delivers CancelledError (a BaseException since Python 3.8) at ANY
        # await point inside the stream loop. A client that aborts a large
        # upload mid-body is the everyday case — an `except Exception` would
        # run the unlink for ordinary failures and then SKIP it for the most
        # common failure of all (cancellation), leaking the staging file of
        # exactly the uploads most likely to be abandoned. SystemExit /
        # KeyboardInterrupt are not expected on this path (the loop is not
        # the main thread's interpreter exit), but letting them through with
        # the file already unlinked is still correct: cleanup happens before
        # the exception continues outward. The crash-orphaned residue this
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
