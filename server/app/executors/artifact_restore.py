"""Best-effort restore of evicted job_dir inputs from object storage.

The local job_dir is an evictable cache (EXEC-ARTIFACT-STORE-001): a
completed job's upstream artifacts may be reclaimed by the maintenance
thread. A targeted rerun that falls back to the local code pool (no online
code Worker) then finds its declared inputs missing. This module streams
them back from the instance object store before the node runs.

Restore is strictly best-effort: per-file failures are logged and the file
stays missing, so the node errors on the absent input itself — a storage
outage never changes node semantics.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from server.app.services.job_artifact_objects import (
    JobArtifactObjectStore,
    valid_artifact_name,
)

logger = logging.getLogger(__name__)

_CHUNK_BYTES = 1024 * 1024


def restore_missing_inputs(
    store: JobArtifactObjectStore | None,
    *,
    job_id: str,
    job_dir: Path,
    inputs: tuple[str, ...],
) -> None:
    """Re-materialize declared inputs that are missing from the job_dir."""
    if store is None:
        return
    for name in inputs:
        if (job_dir / name).is_file():
            continue
        try:
            _restore_one(store, job_id=job_id, job_dir=job_dir, name=name)
        except Exception:
            # #204 broad-except audit: deliberate per-file best-effort restore
            # (module docstring: "a storage outage never changes node
            # semantics"). One input's failure must neither fail the node nor
            # skip restoring the remaining declared inputs — the node errors
            # on the missing input itself, which is the honest outcome. The
            # outcome space is the mixed storage/DB surface of lookup +
            # stream + manifest read, not a business family; exc_info keeps
            # the per-file root cause visible.
            logger.warning(
                "input restore failed for job %s artifact %s; leaving it missing",
                job_id,
                name,
                exc_info=True,
            )


def _restore_one(store: JobArtifactObjectStore, *, job_id: str, job_dir: Path, name: str) -> None:
    if not valid_artifact_name(name):
        logger.warning("refusing to restore unsafe artifact name %r for job %s", name, job_id)
        return
    row = store.lookup(job_id, name)
    if row is None:
        return
    target = job_dir / name
    tmp = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    try:
        with store.open_stream(row) as stream, tmp.open("wb") as out:
            while chunk := stream.read(_CHUNK_BYTES):
                digest.update(chunk)
                out.write(chunk)
    except Exception:
        # #204 broad-except audit: cleanup-guard-then-bare-re-raise (#233
        # pattern — clean up broad, classify never). The partial .part file
        # must be reclaimed on ANY failure mode (streaming I/O, decompress,
        # disk full), and the exception keeps propagating with its original
        # type so the per-file caller above can log the true root cause and
        # leave the input missing. Nothing is masked; the raise is bare.
        tmp.unlink(missing_ok=True)
        raise
    expected = str(row.get("content_hash") or "")
    if expected and digest.hexdigest() != expected:
        tmp.unlink(missing_ok=True)
        logger.warning(
            "restored input %s for job %s failed the content-hash check; leaving it missing",
            name,
            job_id,
        )
        return
    os.replace(tmp, target)
