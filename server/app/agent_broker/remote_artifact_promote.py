"""Promote-phase mechanics for the Worker-direct S3 artifact channel (#160 D12).

Split out of ``remote_artifacts.py`` for the file-size budget: the result-commit
module stays the verify-then-apply orchestrator, this module owns the apply
phase — authority-key copy with rollback backup, then the guarded
registration tail (job-dir file promote + manifest rows in one gated
transaction, ``remote_artifact_gate.py``), staging cleanup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from server.app.agent_broker.remote_artifact_gate import (
    register_remote_rows_guarded,
    restore_authority_backups,
)
from server.app.agent_broker.remote_artifact_support import (
    build_manifest_rows,
    discard_staging,
    promote_remote,
)
from server.app.services.job_artifact_gzip import GZIP_SUFFIX, is_gzip_key
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
    lease_id: str,
) -> bool:
    """Copy to authority keys, promote staged files, register rows, clean up.

    Undeclared names are promoted/registered but never land in the job dir
    (the same whitelist as the tar unpack path). Copies precede row writes:
    a failure between them leaves orphaned authority objects (lifecycle
    backstop), never dangling manifest rows. All manifest rows upsert in ONE
    transaction (the guarded registration): a mid-batch failure rolls back
    instead of leaving a half-registered manifest.

    Re-runs overwrite existing authority keys, so every pre-existing
    authority object is first backed up (server-side copy to a rollback key
    under this execution's staging prefix, no byte downloads). A mid-batch
    copy failure restores the already-overwritten keys from their backups
    (best-effort; a failed restore logs a warning) — otherwise the old
    manifest rows would keep pointing at objects whose bytes no longer match
    the recorded hash/size. Backup keys are cleaned up on every outcome.

    #338: the authority key keeps the staging ref's form marker (``.gz`` or
    bare). A form-changing re-run targets a key that does not exist yet, so
    nothing is overwritten or backed up; the previous-form object stays for
    the still-pointing manifest row until the row upsert retargets it.

    #645 P2-a (EXEC-GENERATION-001 artifact byte plane): the promote runs
    BEFORE the finish generation CAS, so the write gate
    (``lease_artifact_write_current``) guards it twice — a lock-free pre-check
    before any byte copy, and the authoritative re-check inside the
    registration transaction (job-dir file promotion and manifest row writes
    ride the same job-mutation lock, so no reset can intervene between the
    check and the row writes). Returns False when the gate rejects: nothing
    was copied/registered, or the copies were already restored from their
    backups; the caller turns this into the commit path's rejection
    semantics. Staging objects survive a rejected promote exactly like a
    failed one (lifecycle reaps them).
    """
    assert object_store.storage is not None
    storage = object_store.storage
    if not object_store.artifact_write_gate_open(job_id=job_id, lease_id=lease_id):
        logger.info(
            "discarding stale promote pre-copy (lease %s): job=%s node=%s",
            lease_id,
            job_id,
            node_key,
        )
        return False
    authority_keys = {
        name: artifact_storage_key(workspace_id, job_id, name)
        + (GZIP_SUFFIX if is_gzip_key(str(ref["storage_key"])) else "")
        for name, ref in remote.items()
    }
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
            # #204 broad-except audit: compensate-then-bare-re-raise (#233
            # pattern). The batch loop's outcome space is mixed — storage-layer
            # errors (botocore surface), ValueError from Worker-untrusted refs,
            # and programming errors all must roll back the already-overwritten
            # authority keys before propagating; the re-raise preserves the
            # original type for apply_remote_artifact_refs' classification, so
            # nothing is converted or masked.
            restore_authority_backups(storage, promoted, backups, authority_keys)
            raise
        applied = register_remote_rows_guarded(
            object_store,
            build_manifest_rows(
                workspace_id, job_id, node_key, remote, authority_keys, content_hashes
            ),
            job_id=job_id,
            lease_id=lease_id,
            staged=staged,
            job_dir=job_dir,
        )
        if not applied:
            # The stale write gate rejected the registration: a sweep/reset
            # committed between the pre-check and here. Undo the byte copies
            # from the backups so the old (or absent) authority objects keep
            # matching the surviving manifest rows; no job-dir writes and no
            # row registrations happened.
            restore_authority_backups(storage, promoted, backups, authority_keys)
            logger.info(
                "discarded stale promote post-copy (lease %s): job=%s node=%s",
                lease_id,
                job_id,
                node_key,
            )
    finally:
        for backup_key in backups.values():
            discard_staging(object_store, backup_key)
    if not applied:
        return False
    for ref in remote.values():
        discard_staging(object_store, str(ref["storage_key"]))
    return True
