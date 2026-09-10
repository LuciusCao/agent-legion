"""Campaign slice-page query (#532 PR-B): the feeder's keyset slicer.

Split from queries/campaigns.py at its budget ceiling (#209 ratchet — the
sibling-module precedent of studio_publish_requests): the campaign row
lifecycle stays in campaigns.py; this mixin carries the one read the
feeder's filter-form rerun/upgrade dispatch needs.
"""

from __future__ import annotations

from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class CampaignSliceQueriesMixin(ConnectionQueriesMixin):
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
