"""Batched reads for the job artifact reconciler (#714).

``reupload_missing`` used to read one job, one active revision and one
manifest row per artifact inside its per-job loop — O(jobs) independent
statements per hourly pass. These reads let it prefetch per batch of jobs
instead (statements = constant x batches); the manifest read rides the
``job_artifacts`` primary key prefix (``job_id``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class ArtifactReconcileQueriesMixin(ConnectionQueriesMixin):
    def list_recent_completed_node_keys(self, window_days: int) -> dict[str, set[str]]:
        """job_id → node keys with a ``completed`` run finished inside the
        window (first-seen job order)."""
        with self._connect_read() as conn:
            rows = conn.execute(
                "select distinct job_id, node_key from node_runs"
                " where status='completed'"
                " and finished_at > now() - make_interval(days => %s)",
                (window_days,),
            ).fetchall()
        completed: dict[str, set[str]] = {}
        for row in rows:
            completed.setdefault(str(row["job_id"]), set()).add(str(row["node_key"]))
        return completed

    def job_artifact_rows_for_jobs(
        self, job_ids: Sequence[str]
    ) -> dict[tuple[str, str, str], dict[str, Any]]:
        """Manifest rows of ``job_ids`` keyed by the primary key
        ``(job_id, node_key, name)`` — the batch twin of
        ``JobArtifactObjectStore.row_for_node``."""
        if not job_ids:
            return {}
        with self._connect_read() as conn:
            rows = conn.execute(
                "select * from job_artifacts where job_id = any(%s)", (list(job_ids),)
            ).fetchall()
        return {
            (str(row["job_id"]), str(row["node_key"]), str(row["name"])): dict(row) for row in rows
        }
