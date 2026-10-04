from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_filtering import JobListFilter, filter_clauses

# The shape list_jobs_paginated emits (str() of a timestamp, offset stripped);
# an optional offset is tolerated. fromisoformat alone is wider than
# PostgreSQL's input (e.g. any single char as the date/time separator).
_CURSOR_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d{1,6})?([+-]\d{2}:\d{2})?"
)


def parse_job_cursor(cursor: str) -> tuple[str, str]:
    """Split a ``next_cursor`` value into ``(created_at, job_id)``.

    #891: a malformed cursor (no ``|`` separator, empty job id, unparseable
    timestamp) raises ``ValueError`` with a caller-readable message instead of
    reaching SQL, where it surfaced as an unhandled 5xx that integrators are
    told to retry forever. The timestamp text is returned unchanged so the
    query keeps the exact comparison semantics of the emitted cursor.
    """
    created_at, sep, job_id = cursor.partition("|")
    if not sep or not created_at or not job_id:
        raise ValueError("cursor must be the next_cursor value from a previous page")
    try:
        if not _CURSOR_TIMESTAMP.fullmatch(created_at):
            raise ValueError
        datetime.fromisoformat(created_at)
    except ValueError:
        raise ValueError(
            "cursor timestamp is not a valid ISO datetime; pass next_cursor unchanged"
        ) from None
    return created_at, job_id


def list_jobs_paginated(
    job_db: JobQueries,
    workspace_id: str,
    limit: int,
    cursor: str | None = None,
    filter: JobListFilter | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    clauses = ["workspace_id=%s"]
    params: list[Any] = [workspace_id]
    if filter is not None:
        extra_clauses, extra_params = filter_clauses(filter)
        clauses.extend(extra_clauses)
        params.extend(extra_params)
    if cursor:
        created_at, job_id = parse_job_cursor(cursor)
        clauses.append("(created_at < %s or (created_at = %s and id < %s))")
        params.extend([created_at, created_at, job_id])
    where = f" where {' and '.join(clauses)}"
    with job_db._connect_read() as conn:
        rows = conn.execute(
            f"select * from jobs{where} order by created_at desc, id desc limit %s",
            (*params, limit + 1),
        )
        jobs = [dict(row) for row in rows]
    if len(jobs) <= limit:
        return jobs, None
    last = jobs[limit - 1]
    # Strip the UTC offset: the cursor travels in URL query strings where
    # "+" decodes to a space, and the naive value parses back under the
    # connection's UTC session timezone.
    created_at = str(last.get("created_at", "")).removesuffix("+00:00")
    next_cursor = f"{created_at}|{last['id']}"
    return jobs[:limit], next_cursor
