"""Promote-phase mechanics for the Worker-direct S3 artifact channel (#160 D12).

Split out of ``remote_artifacts.py`` for the file-size budget: the result-commit
module stays the verify-then-apply orchestrator, this module owns the apply
phase — authority-key copy with rollback backup, atomic promote into the job
dir, one-transaction manifest registration, staging cleanup.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from server.app.agent_broker.remote_artifact_support import (
    build_manifest_rows,
    discard_staging,
    promote_remote,
)
from server.app.services.job_artifact_objects import (
    JobArtifactObjectStore,
    artifact_staging_key,
    artifact_storage_key,
)

logger = logging.getLogger(__name__)


def promote_all(
    object_store: JobArtifactObjectStore,
    workspace_id: str,
    job_id: str,
    node_key: str,
    job_dir: Path,
    remote: dict[str, Any],
    staged: dict[str, Path],
    content_hashes: dict[str, str],
    execution_id: str,
) -> None:
    """Copy to authority keys, promote staged files, register rows, clean up.

    Undeclared names are promoted/registered but never land in the job dir
    (the same whitelist as the tar unpack path). Copies precede row writes:
    a failure between them leaves orphaned authority objects (lifecycle
    backstop), never dangling manifest rows. All manifest rows upsert in ONE
    transaction (record_remote_many): a mid-batch failure rolls back instead
    of leaving a half-registered manifest.

    Re-runs overwrite existing authority keys, so every pre-existing
    authority object is first backed up (server-side copy to a rollback key
    under this execution's staging prefix, no byte downloads). A mid-batch
    copy failure restores the already-overwritten keys from their backups
    (best-effort; a failed restore logs a warning) — otherwise the old
    manifest rows would keep pointing at objects whose bytes no longer match
    the recorded hash/size. Backup keys are cleaned up on every outcome.
    """
    assert object_store.storage is not None
    storage = object_store.storage
    authority_keys = {name: artifact_storage_key(workspace_id, job_id, name) for name in remote}
    backups: dict[str, str] = {}  # name -> rollback key of the pre-existing object
    for name, authority_key in authority_keys.items():
        if storage.head_object(authority_key) is not None:
            backup_key = artifact_staging_key(
                workspace_id, job_id, execution_id, f".rollback/{name}"
            )
            storage.copy_object(authority_key, backup_key)
            backups[name] = backup_key
    promoted: list[str] = []
    try:
        for name, ref in remote.items():
            promote_remote(
                object_store,
                workspace_id=workspace_id,
                job_id=job_id,
                name=name,
                storage_key=str(ref["storage_key"]),
            )
            promoted.append(name)
    except Exception:
        # Roll back the keys this batch already overwrote; keys without a
        # backup had no prior object (the orphan is lifecycle's backstop).
        for name in promoted:
            rollback_key = backups.get(name)
            if rollback_key is None:
                continue
            try:
                storage.copy_object(rollback_key, authority_keys[name])
            except Exception:
                logger.warning(
                    "failed to roll back artifact object %s", authority_keys[name], exc_info=True
                )
        raise
    finally:
        for backup_key in backups.values():
            discard_staging(object_store, backup_key)
    for name, staged_path in staged.items():
        target = job_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_path, target)
    object_store.record_remote_many(
        build_manifest_rows(workspace_id, job_id, node_key, remote, authority_keys, content_hashes)
    )
    for ref in remote.values():
        discard_staging(object_store, str(ref["storage_key"]))
