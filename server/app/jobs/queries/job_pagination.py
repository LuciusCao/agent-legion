from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_filtering import JobListFilter, filter_clauses


def list_jobs_paginated(
    job_db: JobQueries,
    workspace_id: str,
    limit: int,
    cursor: str | None = None,
    filter: JobListFilter | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    clauses = ["workspace_id=?"]
    params: list[Any] = [workspace_id]
    if filter is not None:
        extra_clauses, extra_params = filter_clauses(filter)
        clauses.extend(extra_clauses)
        params.extend(extra_params)
    if cursor:
        created_at, job_id = cursor.split("|", 1)
        clauses.append("(created_at < ? or (created_at = ? and id < ?))")
        params.extend([created_at, created_at, job_id])
    where = f" where {' and '.join(clauses)}"
    with job_db._connect_read() as conn:
        rows = conn.execute(
            f"select * from jobs{where} order by created_at desc, id desc limit ?",
            (*params, limit + 1),
        )
        jobs = [dict(row) for row in rows]
    if len(jobs) <= limit:
        return jobs, None
    last = jobs[limit - 1]
    next_cursor = f"{last.get('created_at', '')}|{last['id']}"
    return jobs[:limit], next_cursor
