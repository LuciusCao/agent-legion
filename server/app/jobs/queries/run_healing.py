"""Deterministic run-id derivation + failed-run healing (#501, PR #497).

Split out of ``batch.py`` for the file-size budget. The digest derivation is
single-sourced here so ``create_run``'s upsert and the all-duplicates healing
path resolve identical input to the SAME row — two copies of the formula
would drift silently.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from server.app.jobs.queries.batch_queue import backfill_deprecated_workflow_key
from server.app.jobs.queries.connection import ConnectionQueriesMixin


def deterministic_run_id(
    workspace_id: str, workflow_key: str, source_kind: str, digest_payload: dict[str, Any]
) -> str:
    """The run id ``create_run`` derives from identical input.

    Re-submitting the SAME items must resolve to the same row both in
    ``create_run``'s upsert and in callers that need to look the row up
    BEFORE deciding to upsert (the all-duplicates healing path in
    run_service).
    """
    payload_json = json.dumps(digest_payload, ensure_ascii=False, sort_keys=True)
    payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:16]
    return f"{workspace_id}_{workflow_key}_{source_kind}_{payload_digest}"


class RunHealingQueriesMixin(ConnectionQueriesMixin):
    """The #501 all-duplicates healing query (composed into JobQueries)."""

    def heal_failed_run_if_duplicate(
        self,
        workspace_id: str,
        workflow_key: str,
        source_kind: str,
        digest_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Heal the deterministic-id run when it sits failed (#501).

        The all-duplicates resubmission path: identical items resolve to the
        same run id as the submission that failed partway; an atomic guarded
        UPDATE (matching only status='failed') flips it back to 'created',
        clears error_message, and aligns created_count with the run's live
        job count. None = no failed run under that id (caller keeps the
        legacy "No tasks were resolved" 400). The status matches the
        partial-resume healing contract (RUN_UPSERT_CONFLICT's
        failed→created arm), not 'completed' — the run's jobs may still be
        mid-flight and 'created' is the honest state for that.
        """
        run_id = deterministic_run_id(workspace_id, workflow_key, source_kind, digest_payload)
        with self.connect() as conn:
            row = conn.execute(
                """
                update runs
                set status='created', error_message='',
                    created_count=(select count(*) from jobs where run_id=%s),
                    updated_at=current_timestamp
                where id=%s and workspace_id=%s and status='failed'
                returning *
                """,
                (run_id, run_id, workspace_id),
            ).fetchone()
        if row is None:
            return None
        return backfill_deprecated_workflow_key(dict(row))
