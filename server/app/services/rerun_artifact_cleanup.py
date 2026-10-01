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
authority copy alive (#683 review P1): the batch probe at entry skips keys
already re-registered when cleanup starts, and the per-object re-probe
re-checks the CURRENT manifest immediately before each removal — a key that
reappeared inside the batch probe→remove gap belongs to the new attempt. The
residual per-object window (re-probe → that object's removal call,
milliseconds) is only closable by conditional removal or versioned keys; the
post-removal re-check keeps it diagnosable instead of silent.
``delete_rerun_artifact_objects`` never raises (#759 P1): the cleanup runs
after the mutation transaction committed, so a store/probe failure is logged,
not propagated — a 500 would misreport the committed success and, in batch
callers, abort the remaining jobs.

Every probe is a targeted existence query (``live_keys_for``: job_id + the
retired keys, #706 review P2): each removal re-verifies its key against the
live manifest WITHOUT shipping the manifest — a full ``rows_for_job`` read
per retired object degraded multi-artifact reruns to O(retired × manifest
rows) on the sync request path, ahead of ``notify_schedulable_work()``.
"""

from __future__ import annotations

import logging
from typing import Any

from server.app.services.job_artifact_guarded_delete import delete_retired_objects

logger = logging.getLogger(__name__)


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
    manifest row last, so a key can reappear between the batch probe and
    the removals — a snapshot-blind removal would strand the fresh manifest
    row on a nonexistent object. Each probe ships only the retired keys,
    never the whole manifest (#706 review P2).

    Never raises (#759 P1): see the module docstring — a cleanup failure is
    logged with the job_id and operation, not propagated."""
    if object_store is None or not getattr(object_store, "enabled", False) or not deleted_rows:
        return
    try:
        delete_retired_objects(object_store, deleted_rows, job_id, operation)
    except Exception:
        # #204 broad-except audit: post-commit best-effort teardown. The
        # rerun/upgrade/run-to transaction has COMMITTED by the time this
        # runs — the operation succeeded — so a failure here (object-store
        # SDK/network errors from live_keys_for/delete_objects, probe races;
        # not a business family this module could enumerate) must not
        # retroactively fail the committed mutation: a 500 would misreport
        # the success and, in batch callers, abort the remaining jobs
        # (#759 P1). The residue is orphaned objects, which the bucket
        # lifecycle rule reaps — never a listed-but-stale artifact (the
        # manifest rows are already gone). logger.exception keeps the
        # traceback with the job_id and the calling operation's domain.
        logger.exception("rerun %s cleanup failed for job %s", operation, job_id)
