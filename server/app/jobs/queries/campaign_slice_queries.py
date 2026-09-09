"""Campaign queries split from queries/campaigns.py at its budget ceiling
(#209 ratchet — the sibling-module precedent of studio_publish_requests):
the campaign row lifecycle stays in campaigns.py; this mixin carries the
reads beyond the row itself — the feeder's keyset slicer (PR-B) and the
submit-mode run-overview aggregation (PR-C).
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin

# Per-run job-count summary for the campaign detail view: rides the
# trigger-maintained counter table (DB-RUN-JOB-STATUS-COUNTS-001) instead
# of a group-by over each run's whole jobs slice.
_CAMPAIGN_RUN_OVERVIEW_SQL = """
select r.id, r.status, r.created_count,
       coalesce(sum(c.cnt), 0) as job_count
from runs r
left join run_job_status_counts c on c.run_id = r.id and c.cnt <> 0
where r.campaign_id = %s
group by r.id, r.status, r.created_count
order by r.created_at desc, r.id desc
limit %s
"""


class CampaignSliceQueriesMixin(ConnectionQueriesMixin):
    def list_campaign_runs_overview(
        self, campaign_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Submit-mode run overview for the campaign detail endpoint (PR-C).

        Rides the idx_runs_campaign partial index (v80: campaign_id <> ''
        rows only); the per-run job totals come from the counter table, so
        the cost tracks the run count (bounded by batches_submitted), never
        the campaign's job volume. Runs whose jobs were completed by a
        manual submission still appear here — the deterministic run id made
        the campaign's batch and that manual run the same row.
        """
        with self._connect_read() as conn:
            rows = conn.execute(_CAMPAIGN_RUN_OVERVIEW_SQL, (campaign_id, limit)).fetchall()
        return [
            {
                "id": str(row["id"]),
                "status": str(row["status"]),
                "created_count": int(row["created_count"]),
                "job_count": int(row["job_count"]),
            }
            for row in rows
        ]

    def list_campaign_filter_job_ids_page(
        self,
        workspace_id: str,
        job_filter: Any,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[str], str | None]:
        """One keyset page of job ids matching ``job_filter``, newest first.

        The feeder's rerun/upgrade filter-mode slicer (design §1.4): the SQL
        shape is the resolver's ``_list_job_ids_page`` keyset semantics
        (``order by created_at desc, id desc`` + the ``created_at|id``
        composite cursor) expressed as a JobQueries method, so the campaign
        worker never opens its own connection (BOUNDARY-DATA-001). The page
        is exactly ``limit`` ids or fewer; the second element is the next
        cursor, None at exhaustion.
        """
        from server.app.jobs.queries.job_filtering import filter_clauses

        clauses, filter_params = filter_clauses(job_filter)
        where = f" where workspace_id = %s{''.join(f' and {c}' for c in clauses)}"
        params: list[Any] = [workspace_id, *filter_params]
        if cursor:
            created_at, job_id = cursor.split("|", 1)
            where += " and (created_at < %s or (created_at = %s and id < %s))"
            params.extend([created_at, created_at, job_id])
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select id, created_at from jobs{where}"
                " order by created_at desc, id desc limit %s",
                (*params, limit + 1),
            ).fetchall()
        page = [(str(row["id"]), str(row["created_at"])) for row in rows]
        if len(page) <= limit:
            return [job_id for job_id, _created_at in page], None
        last_id, last_created_at = page[limit - 1]
        return [job_id for job_id, _created_at in page[:limit]], f"{last_created_at}|{last_id}"
