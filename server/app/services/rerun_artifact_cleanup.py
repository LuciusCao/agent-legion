"""Post-commit object removal for manifest rows a rerun retired (#508).

Split from ``job_staged_cleanup.py`` (file budget; same seam as
job_deletion's helpers): the revalidation walk and the post-deletion
diagnostic re-check live here, the commit/teardown helper stays there.

Ordering facts this module encodes (review R1): ``promote_all`` copies onto
the authority key FIRST and writes the ``job_artifacts`` rows LAST (a
mid-batch failure leaves orphans, never dangling rows) — so a manifest read
can miss a row whose fresh object copy already landed. The rerun transaction
commits the node reset and the manifest-row removal together; from that
instant the job is schedulable again, so a re-attempt's ``promote_all`` can
interleave with this cleanup at any point. Two guard layers keep the fresh
authority copy alive (#683 review P1): the batch re-read at entry skips keys
already re-registered when cleanup starts, and the per-object re-read
re-checks the CURRENT manifest immediately before each removal — a key that
reappeared inside the batch read→remove gap belongs to the new attempt. The
residual per-object window (re-read → that object's removal call,
milliseconds) is only closable by conditional removal or versioned keys; the
post-removal re-check keeps it diagnosable instead of silent.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _live_keys(object_store: Any, job_id: str) -> set[str]:
    """Current manifest keys ({} when the store exposes no query seam)."""
    return {
        str(row["storage_key"])
        for row in getattr(object_store, "rows_for_job", lambda _job: [])(job_id)
    }


def delete_rerun_artifact_objects(
    object_store: Any,
    deleted_rows: list[dict[str, Any]],
    job_id: str,
    operation: str,
) -> None:
    """Best-effort object removal for manifest rows a rerun just dropped
    (#508). Mirrors job_deletion's post-commit ordering: the rows are gone
    from ``job_artifacts`` inside the committed transaction, so a failed
    removal only leaves an orphan the bucket lifecycle rule reaps — never a
    listed-but-stale artifact. No object store (None / disabled) = no-op.

    Between the rerun transaction's commit and this cleanup the job is
    schedulable again — a fast re-attempt may register a NEW manifest row
    with the same stable authority key. Re-validate every row against the
    CURRENT manifest before its removal: a reappeared key belongs to the
    new attempt and is skipped. The re-check runs PER OBJECT, immediately
    before that object's removal (#683 review P1): ``promote_all`` copies
    the fresh object onto the authority key first and registers its
    manifest row last, so a key can reappear between the batch snapshot
    read and the removals — a snapshot-blind removal would strand the
    fresh manifest row on a nonexistent object."""
    if object_store is None or not getattr(object_store, "enabled", False) or not deleted_rows:
        return
    live = _live_keys(object_store, job_id)
    stale_rows = [row for row in deleted_rows if str(row["storage_key"]) not in live]
    if len(stale_rows) < len(deleted_rows):
        logger.info(
            "rerun %s cleanup for job %s skipped %d re-registered object(s)",
            operation,
            job_id,
            len(deleted_rows) - len(stale_rows),
        )
    spared: set[str] = set()
    deleted_keys: set[str] = set()
    for row in stale_rows:
        # Re-validate against the CURRENT manifest immediately before this
        # object's removal: a re-attempt completing promote_all after the
        # snapshot read above re-registers this same stable authority key,
        # and removing it would strand the fresh manifest row.
        key = str(row["storage_key"])
        if key in _live_keys(object_store, job_id):
            spared.add(key)
            continue
        object_store.delete_objects([row])
        deleted_keys.add(key)
    if spared:
        logger.info(
            "rerun %s cleanup for job %s spared %d object(s) re-registered during cleanup: %s",
            operation,
            job_id,
            len(spared),
            sorted(spared)[:5],
        )
    # Post-removal re-check: a row appearing under a removed key in the
    # residual per-object window means the race fired — surface it (bucket
    # lifecycle cannot repair a stranded manifest row).
    raced = _live_keys(object_store, job_id) & deleted_keys
    if raced:
        logger.warning(
            "rerun %s cleanup for job %s: %d manifest row(s) appeared under "
            "just-deleted object key(s) %s — re-attempt raced the cleanup; "
            "re-run the node or re-upload to repair the authority copy",
            operation,
            job_id,
            len(raced),
            sorted(raced)[:5],
        )
