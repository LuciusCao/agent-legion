"""Resolve workspace job list filters into concrete job ids for batch operations."""

from __future__ import annotations

import logging
import os
from collections.abc import Collection, Sequence
from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_filtering import JobListFilter, filter_clauses

_PAGE_SIZE = 1000
MAX_BATCH_JOBS_ENV = "AGENT_LEGION_MAX_BATCH_JOBS"
logger = logging.getLogger(__name__)

if os.environ.get(MAX_BATCH_JOBS_ENV) is not None:
    logger.warning(
        "%s is deprecated and ignored; batch selections are no longer capped", MAX_BATCH_JOBS_ENV
    )


class EmptyJobSelectionError(ValueError):
    """Raised when a batch operation's selection resolves to zero jobs."""


def resolve_batch_selection(
    job_db: JobQueries,
    workspace_id: str,
    job_ids: Sequence[str] | None,
    job_filter: JobListFilter | None = None,
    exclude_ids: Collection[str] = (),
) -> list[str]:
    """Return explicit ids, or resolve ``job_filter`` minus ``exclude_ids``."""
    if job_filter is None:
        return list(job_ids or [])
    ids: list[str] = []
    cursor: str | None = None
    while True:
        page, cursor, _ = resolve_job_ids_page(
            job_db, workspace_id, job_filter, _PAGE_SIZE, cursor=cursor, exclude_ids=exclude_ids
        )
        ids.extend(page)
        if cursor is None:
            return ids


def resolve_job_ids_page(
    job_db: JobQueries,
    workspace_id: str,
    f: JobListFilter,
    limit: int,
    *,
    cursor: str | None = None,
    freeze_at: str | None = None,
    exclude_ids: Collection[str] = (),
) -> tuple[list[str], str | None, str | None]:
    """Return (ids, next cursor, frozen upper bound) for one keyset page."""
    rows, next_cursor = _list_job_ids_page(job_db, workspace_id, f, limit, cursor, freeze_at)
    if freeze_at is None and cursor is None and rows:
        job_id, created_at = rows[0]
        freeze_at = f"{created_at}|{job_id}"
    excluded = {value.strip() for value in exclude_ids if value.strip()}
    return [job_id for job_id, _ in rows if job_id not in excluded], next_cursor, freeze_at


def _list_job_ids_page(
    job_db: JobQueries,
    workspace_id: str,
    f: JobListFilter,
    limit: int,
    cursor: str | None,
    freeze_at: str | None = None,
) -> tuple[list[tuple[str, str]], str | None]:
    """Narrow keyset page of (id, created_at) pairs ordered newest first."""
    clauses, filter_params = filter_clauses(f)
    where = f" where workspace_id = %s{''.join(f' and {c}' for c in clauses)}"
    params: list[Any] = [workspace_id, *filter_params]
    if freeze_at:
        created_at, job_id = freeze_at.split("|", 1)
        where += " and (created_at, id) <= (%s, %s)"
        params.extend([created_at, job_id])
    if cursor:
        created_at, job_id = cursor.split("|", 1)
        where += " and (created_at, id) < (%s, %s)"
        params.extend([created_at, job_id])
    with job_db._connect_read() as conn:
        rows = conn.execute(
            f"select id, created_at from jobs{where} order by created_at desc, id desc limit %s",
            (*params, limit + 1),
        ).fetchall()
    page = [(str(row["id"]), str(row["created_at"])) for row in rows]
    if len(page) <= limit:
        return page, None
    last_id, last_created_at = page[limit - 1]
    return page[:limit], f"{last_created_at}|{last_id}"
