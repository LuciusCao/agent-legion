"""Read-only, keyset-paged artifact recovery scans (#1019).

The CLI binds this facade to ``default_transaction_read_only=on`` and bypasses
``JobQueriesBase.__init__`` so schema bootstrap never runs. Each page reads its
jobs and all supporting rows on one connection/transaction snapshot; no row
locks or mutation helpers are used.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from server.app.jobs.queries.artifact_manifest_lock import try_artifact_manifest_lock
from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.jobs.queries.upgrade_impl_identity import (
    try_acquire_implementation_publication_lock,
)

if TYPE_CHECKING:
    from server.app.jobs.queries import JobQueries

_JOB_COLUMNS = """
  j.id, j.workspace_id, j.status, j.execution_generation, j.created_at, j.storage_dir,
  j.workflow_revision_id, j.workflow_version, j.workflow_definition_hash,
  j.workflow_definition_snapshot_json, j.execution_mode, j.target_node_key
"""


def _filters(
    workspace_ids: Sequence[str] | None,
    job_ids: Sequence[str] | None,
    after: tuple[Any, str] | None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if workspace_ids is not None:
        clauses.append("j.workspace_id = ANY(%s)")
        params.append(sorted(set(workspace_ids)))
    if job_ids is not None:
        clauses.append("j.id = ANY(%s)")
        params.append(sorted(set(job_ids)))
    if after is not None:
        clauses.append("(j.created_at, j.id) > (%s, %s)")
        params.extend(after)
    return (f" where {' and '.join(clauses)}" if clauses else ""), params


def _job_support(
    conn: Any, jobs: list[dict[str, Any]], *, lock: bool = False
) -> list[dict[str, Any]]:
    if not jobs:
        return []
    ids = [str(job["id"]) for job in jobs]
    workspace_keys = sorted({str(job["workspace_id"]) for job in jobs})
    suffix = " for update" if lock else ""
    artifacts = [
        dict(row)
        for row in conn.execute(
            "select * from job_artifacts where job_id = ANY(%s)"
            f" order by job_id, uploaded_at, node_key{suffix}",
            (ids,),
        ).fetchall()
    ]
    nodes = [
        dict(row)
        for row in conn.execute(
            "select job_id, node_key, status from job_nodes"
            f" where job_id = ANY(%s) order by job_id, node_key{suffix}",
            (ids,),
        ).fetchall()
    ]
    pending_shards = [
        dict(row)
        for row in conn.execute(
            "select distinct job_id, node_key from node_shards"
            " where job_id = ANY(%s) and status='pending' order by job_id, node_key",
            (ids,),
        ).fetchall()
    ]
    revisions = [
        dict(row)
        for row in conn.execute(
            "select id, workspace_id, version, definition_hash, definition_json, status"
            " from workflow_revisions where workspace_id = ANY(%s) and status='active'"
            " order by workspace_id, version desc",
            (workspace_keys,),
        ).fetchall()
    ]
    artifacts_by_job: dict[str, list[dict[str, Any]]] = {job_id: [] for job_id in ids}
    for row in artifacts:
        artifacts_by_job[str(row["job_id"])].append(row)
    nodes_by_job: dict[str, list[dict[str, Any]]] = {job_id: [] for job_id in ids}
    for row in nodes:
        nodes_by_job[str(row["job_id"])].append(row)
    shards_by_job: dict[str, list[str]] = {job_id: [] for job_id in ids}
    for row in pending_shards:
        shards_by_job[str(row["job_id"])].append(str(row["node_key"]))
    revisions_by_workspace = {str(row["workspace_id"]): row for row in revisions}
    return [
        {
            **job,
            "artifacts": artifacts_by_job[str(job["id"])],
            "nodes": nodes_by_job[str(job["id"])],
            "pending_shard_nodes": shards_by_job[str(job["id"])],
            "active_revision": revisions_by_workspace.get(str(job["workspace_id"])),
        }
        for job in jobs
    ]


class ArtifactDanglingQueriesMixin(ConnectionQueriesMixin):
    def artifact_dangling_job_page(
        self,
        *,
        after: tuple[Any, str] | None = None,
        limit: int = 100,
        workspace_ids: Sequence[str] | None = None,
        job_ids: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """One bounded page ordered by ``(created_at, id)`` with scan inputs."""
        if not 1 <= limit <= 500:
            raise ValueError("artifact dangling page size must be between 1 and 500")
        if workspace_ids is not None and not workspace_ids:
            return []
        if job_ids is not None and not job_ids:
            return []
        where, params = _filters(workspace_ids, job_ids, after)
        with self._connect_read() as conn:
            conn.execute("begin transaction isolation level repeatable read read only")
            jobs = [
                dict(row)
                for row in conn.execute(
                    f"select {_JOB_COLUMNS} from jobs j{where}"
                    " order by j.created_at, j.id limit %s",
                    [*params, limit],
                ).fetchall()
            ]
            return _job_support(conn, jobs)

    @staticmethod
    def artifact_dangling_job_in_transaction(conn: Any, job_id: str) -> dict[str, Any] | None:
        """Read and row-lock the same complete job projection in a mutation transaction."""
        row = conn.execute(f"select {_JOB_COLUMNS} from jobs j where j.id=%s", (job_id,)).fetchone()
        if row is None:
            return None
        return _job_support(conn, [dict(row)], lock=True)[0]

    @staticmethod
    def try_artifact_recovery_final_locks_in_transaction(
        conn: Any, job_id: str, workspace_id: str
    ) -> bool:
        """Try final manifest→publication locks in their declared order."""
        if not try_artifact_manifest_lock(conn, job_id):
            return False
        return try_acquire_implementation_publication_lock(conn, workspace_id)


def artifact_dangling_reader_from_dsn(dsn: str) -> JobQueries:
    """Build the normal facade without ``init_db`` on a server-read-only DSN."""
    from server.app.jobs.queries import JobQueries
    from server.app.jobs.queries.agent_backfill_reader import read_only_dsn

    reader = JobQueries.__new__(JobQueries)
    reader._path = read_only_dsn(dsn)
    return reader
