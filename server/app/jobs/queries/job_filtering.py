from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from server.app.jobs import JobQueries

# Mirrors the frontend normalizeJobStatus folding: any status outside the
# known non-pending set (queued or anything unknown) is surfaced as "pending".
_NON_PENDING_STATUSES = ("running", "completed", "failed", "paused")

_STATUS_BUCKET_SQL = (
    "case when status in ('running', 'completed', 'failed', 'paused')"
    " then status else 'pending' end"
)

_ACTIVE_NODE_CLAUSE = """
(exists (
  select 1 from job_nodes jn
  where jn.job_id = jobs.id and jn.status = 'running' and jn.node_key = %s
) or (
  not exists (
    select 1 from job_nodes jn2
    where jn2.job_id = jobs.id and jn2.status = 'running'
  )
  and %s = (
    select jn3.node_key from job_nodes jn3
    where jn3.job_id = jobs.id and jn3.status = 'failed'
    order by jn3.id limit 1
  )
))
"""


@dataclass(frozen=True)
class JobListFilter:
    status: str | None = None
    search: str | None = None
    workflow_version: int | None = None
    workflow_version_none: bool = False
    active_node_key: str | None = None
    packed: int | None = None


def _escape_like(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def filter_clauses(f: JobListFilter) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if f.status:
        if f.status == "pending":
            clauses.append("status not in ('running', 'completed', 'failed', 'paused')")
        else:
            clauses.append("status = %s")
            params.append(f.status)
    if f.search:
        term = f"%{_escape_like(f.search.strip())}%"
        clauses.append(
            "(id ilike %s escape '\\' or source_id ilike %s escape '\\'"
            " or batch_id ilike %s escape '\\' or title ilike %s escape '\\')"
        )
        params.extend([term, term, term, term])
    if f.workflow_version is not None:
        clauses.append("workflow_version = %s")
        params.append(f.workflow_version)
    elif f.workflow_version_none:
        clauses.append("workflow_version is null")
    if f.active_node_key:
        clauses.append(_ACTIVE_NODE_CLAUSE)
        params.extend([f.active_node_key, f.active_node_key])
    if f.packed is not None:
        clauses.append("packed = %s")
        params.append(f.packed)
    return clauses, params


def _where(workspace_id: str, f: JobListFilter) -> tuple[str, list[Any]]:
    clauses, params = filter_clauses(f)
    where = f" where workspace_id = %s{''.join(f' and {c}' for c in clauses)}"
    return where, [workspace_id, *params]


def count_jobs_filtered(job_db: JobQueries, workspace_id: str, f: JobListFilter) -> int:
    where, params = _where(workspace_id, f)
    with job_db._connect_read() as conn:
        row = conn.execute(f"select count(*) as cnt from jobs{where}", params).fetchone()
    return int(row["cnt"]) if row is not None else 0


def job_facets(job_db: JobQueries, workspace_id: str, f: JobListFilter) -> dict[str, Any]:
    """Facet counts with exclude-own-dimension semantics.

    Each dimension's counts apply every filter except that dimension's own,
    matching the frontend's facet ``exclude`` behavior. ``version_counts``
    keeps the null version under the ``None`` key and ``node_counts`` keeps
    jobs without a running/failed node under the ``None`` key; serialization
    to response keys happens in the service layer.
    """
    total = count_jobs_filtered(job_db, workspace_id, f)

    status_where, status_params = _where(workspace_id, replace(f, status=None))
    with job_db._connect_read() as conn:
        rows = conn.execute(
            f"select {_STATUS_BUCKET_SQL} as bucket, count(*) as cnt"
            f" from jobs{status_where} group by 1",
            status_params,
        )
        status_counts = {str(row["bucket"]): int(row["cnt"]) for row in rows}

    version_where, version_params = _where(
        workspace_id, replace(f, workflow_version=None, workflow_version_none=False)
    )
    with job_db._connect_read() as conn:
        rows = conn.execute(
            f"select workflow_version, count(*) as cnt from jobs{version_where}"
            " group by workflow_version",
            version_params,
        )
        version_counts = {row["workflow_version"]: int(row["cnt"]) for row in rows}

    node_where, node_params = _where(workspace_id, replace(f, active_node_key=None))
    with job_db._connect_read() as conn:
        rows = conn.execute(
            "select coalesce(r.node_key, f.node_key) as active_node_key, count(*) as cnt"
            " from jobs"
            " left join lateral ("
            "select node_key from job_nodes"
            " where job_id = jobs.id and status = 'running' order by id limit 1"
            ") r on true"
            " left join lateral ("
            "select node_key from job_nodes"
            " where job_id = jobs.id and status = 'failed' order by id limit 1"
            ") f on r.node_key is null"
            f"{node_where}"
            " group by 1",
            node_params,
        )
        node_counts = {row["active_node_key"]: int(row["cnt"]) for row in rows}

    return {
        "total": total,
        "status_counts": status_counts,
        "version_counts": version_counts,
        "node_counts": node_counts,
    }
