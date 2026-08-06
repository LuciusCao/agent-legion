"""Resolve workspace job list filters into concrete job ids for batch operations."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Any

from server.app.jobs import JobQueries
from server.app.jobs.queries.job_filtering import JobListFilter, filter_clauses

# Batch selection resolves synchronously in the request thread (documented
# limitation); keyset pages keep memory bounded while scanning workspaces.
_PAGE_SIZE = 1000


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
    return resolve_job_ids(job_db, workspace_id, job_filter, exclude_ids)


def resolve_job_ids(
    job_db: JobQueries,
    workspace_id: str,
    f: JobListFilter,
    exclude_ids: Collection[str] = (),
) -> list[str]:
    """Collect every job id matching ``f``, newest first, minus excluded ids."""
    excluded = {value.strip() for value in exclude_ids if value.strip()}
    ids: list[str] = []
    cursor: str | None = None
    while True:
        page, cursor = _list_job_ids_page(job_db, workspace_id, f, _PAGE_SIZE, cursor)
        ids.extend(job_id for job_id, _created_at in page if job_id not in excluded)
        if cursor is None:
            return ids


def _list_job_ids_page(
    job_db: JobQueries,
    workspace_id: str,
    f: JobListFilter,
    limit: int,
    cursor: str | None,
) -> tuple[list[tuple[str, str]], str | None]:
    """Narrow keyset page of (id, created_at) pairs ordered newest first."""
    clauses, filter_params = filter_clauses(f)
    where = f" where workspace_id = %s{''.join(f' and {c}' for c in clauses)}"
    params: list[Any] = [workspace_id, *filter_params]
    if cursor:
        created_at, job_id = cursor.split("|", 1)
        where += " and (created_at < %s or (created_at = %s and id < %s))"
        params.extend([created_at, created_at, job_id])
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
