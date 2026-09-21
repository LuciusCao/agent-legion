"""Promote-phase orchestration for the Worker-direct S3 artifact channel (#160 D12).

Split out of ``remote_artifacts.py`` for the file-size budget: the result-commit
module stays the verify-then-apply orchestrator; this module owns the apply
phase's per-execution wiring — authority/staging/rollback key derivation and
the entry pre-check — and delegates the byte-plane sequence itself (rollback
backup → staging→authority copy → in-transaction gated registration → restore
on rejection, the whole per-key sequence serialized by a per-authority-key
advisory lock) to the shared primitive
``executors._artifact_promotion.promote_to_authority_guarded`` (#759 review
P1-B), which the local lease-arm upload uses identically.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from server.app.agent_broker.remote_artifact_support import build_manifest_rows
from server.app.executors._artifact_promotion import (
    AuthorityCopy,
    discard_object,
    promote_to_authority_guarded,
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
    copy failure or a rejected registration restores the already-overwritten
    keys from their backups (best-effort; a failed restore logs a warning) —
    otherwise the old manifest rows would keep pointing at objects whose
    bytes no longer match the recorded hash/size. Backup keys are cleaned up
    on every outcome.

    #338: the authority key keeps the staging ref's form marker (``.gz`` or
    bare). A form-changing re-run targets a key that does not exist yet, so
    nothing is overwritten or backed up; the previous-form object stays for
    the still-pointing manifest row until the row upsert retargets it.

    #645 P2-a (EXEC-GENERATION-001 artifact byte plane): the promote runs
    BEFORE the finish generation CAS, so the write gate
    (``lease_artifact_write_current``) guards it twice — a lock-free pre-check
    here before any byte copy, and the authoritative re-check inside the
    shared primitive's registration transaction (job-dir file promotion and
    manifest row writes ride the same job-mutation lock, so no reset can
    intervene between the check and the row writes). Returns False when the
    gate rejects: nothing was copied/registered, or the copies were already
    restored from their backups; the caller turns this into the commit
    path's rejection semantics. Staging objects survive a rejected promote
    exactly like a failed one (lifecycle reaps them).
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
    registered = promote_to_authority_guarded(
        storage,
        object_store.database_dsn,
        job_id=job_id,
        lease_id=lease_id,
        copies=[
            AuthorityCopy(
                name=name,
                staging_key=str(ref["storage_key"]),
                authority_key=authority_keys[name],
                rollback_key=artifact_staging_key(
                    workspace_id, job_id, execution_id, f".rollback/{name}"
                ),
            )
            for name, ref in remote.items()
        ],
        rows=build_manifest_rows(
            workspace_id, job_id, node_key, remote, authority_keys, content_hashes
        ),
        staged_files=staged,
        job_dir=job_dir,
    )
    if registered is None:
        # The stale write gate rejected the registration: a sweep/reset
        # committed between the pre-check and here. The shared primitive
        # already restored the byte copies from their backups, so the old (or
        # absent) authority objects keep matching the surviving manifest rows;
        # no job-dir writes and no row registrations happened.
        logger.info(
            "discarded stale promote post-copy (lease %s): job=%s node=%s",
            lease_id,
            job_id,
            node_key,
        )
        return False
    for ref in remote.values():
        discard_object(storage, str(ref["storage_key"]))
    return True
