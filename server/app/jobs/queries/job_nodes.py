from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from server.app.jobs.queries.job_node_runs import JobNodeRunQueriesMixin
from server.app.jobs.storage_layout import job_storage_dir
from server.app.storage_paths import make_data_relative


def _job_id(workspace_id: str, workflow_key: str, source_id: str) -> str:
    safe_source_id = source_id.strip().replace("/", "_")
    return f"{workspace_id}_{workflow_key}_{safe_source_id}"


class JobNodeQueriesMixin(JobNodeRunQueriesMixin):
    jobs_dir: Path

    def create_job(
        self,
        workflow_key: str,
        source_type: str,
        source_id: str,
        run_id: str,
        title: str,
        node_keys: list[str],
        workspace_id: str,
        stem: str = "",
        workflow_revision_id: str = "",
        workflow_version: int | None = None,
        workflow_definition_hash: str = "",
        workflow_definition_snapshot_json: str = "",
    ) -> dict[str, Any]:
        job_id = _job_id(workspace_id, workflow_key, source_id)
        storage_dir = job_storage_dir(self.jobs_dir, workspace_id, job_id)
        storage_dir.mkdir(parents=True, exist_ok=True)

        with self.connect() as conn:
            existing = conn.execute("select * from jobs where id=%s", (job_id,)).fetchone()
            if existing is not None and (
                existing["workspace_id"] != workspace_id
                or existing["source_type"] != source_type
                or existing["source_id"] != source_id
            ):
                raise ValueError(f"Job identity collision for {job_id}")
            conn.execute(
                """
                insert into jobs(
                  id, workspace_id, source_type, source_id, run_id, title, storage_dir, stem,
                  workflow_revision_id, workflow_version, workflow_definition_hash, workflow_definition_snapshot_json
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict(id) do update set
                  title=excluded.title,
                  stem=excluded.stem,
                  run_id=excluded.run_id,
                  updated_at=current_timestamp
                """,
                (
                    job_id,
                    workspace_id,
                    source_type,
                    source_id,
                    run_id,
                    title,
                    make_data_relative(storage_dir, self.jobs_dir.parent),
                    stem,
                    workflow_revision_id,
                    workflow_version,
                    workflow_definition_hash,
                    workflow_definition_snapshot_json,
                ),
            )
            for node_key in node_keys:
                conn.execute(
                    """
                    insert into job_nodes(job_id, node_key, status, created_at)
                    values (%s, %s, 'pending', current_timestamp)
                    on conflict(job_id, node_key) do nothing
                    """,
                    (job_id, node_key),
                )
            row = conn.execute("select * from jobs where id=%s", (job_id,)).fetchone()
        if row is None:
            raise RuntimeError("job upsert did not return a row")
        return dict(row)

    def list_jobs(
        self,
        workflow_key: str | None = None,
        status: str | None = None,
        workspace_id: str | None = None,
        source_id: str | None = None,
        status_not_in: Sequence[str] | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        # workflow_key is inert (#211 M2 dropped the column): callers may keep
        # passing it, but jobs are keyed on workspace_id alone.
        del workflow_key
        clauses: list[str] = []
        params: list[Any] = []
        for col, val in (
            ("workspace_id", workspace_id),
            ("status", status),
            ("source_id", source_id),
        ):
            if val:
                clauses.append(f"{col}=%s")
                params.append(val)
        if status_not_in:
            clauses.append(f"status not in ({','.join('%s' for _ in status_not_in)})")
            params.extend(status_not_in)
        where = f" where {' and '.join(clauses)}" if clauses else ""
        # #272: the legacy unbounded list (select * including KB-scale TEXT
        # columns) needs a hard cap. The frontend already uses the paginated
        # /jobs/snapshot endpoint; this bound is API-compat protection only.
        params.append(max(1, min(limit, 500)))
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select * from jobs{where} order by created_at desc limit %s", params
            )
            return [dict(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute("select * from jobs where id=%s", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs_by_ids(self, workspace_id: str, job_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not job_ids:
            return []
        params = [workspace_id, *(str(job_id) for job_id in job_ids)]
        sql = f"select * from jobs where workspace_id=%s and id in ({','.join('%s' for _ in job_ids)})"
        with self._connect_read() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def update_job_status(self, job_id: str, status: str, error_message: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update jobs
                set status=%s, error_message=%s, updated_at=current_timestamp
                where id=%s
                """,
                (status, error_message, job_id),
            )

    def update_job_outcome(self, job_id: str, outcome: str) -> None:
        with self.connect() as conn:
            conn.execute("update jobs set outcome=%s where id=%s", (outcome, job_id))

    def list_job_nodes(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from job_nodes where job_id=%s order by id", (job_id,))
            return [dict(row) for row in rows]

    def list_job_nodes_for_jobs(self, job_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        if not job_ids:
            return {}
        placeholders = ",".join("%s" for _ in job_ids)
        grouped: dict[str, list[dict[str, Any]]] = {job_id: [] for job_id in job_ids}
        with self._connect_read() as conn:
            rows = conn.execute(
                f"select * from job_nodes where job_id in ({placeholders}) order by job_id, id",
                list(job_ids),
            ).fetchall()
        for row in rows:
            grouped[str(row["job_id"])].append(dict(row))
        return grouped

    def get_job_node(self, job_id: str, node_key: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                "select * from job_nodes where job_id=%s and node_key=%s",
                (job_id, node_key),
            ).fetchone()
        return dict(row) if row else None

    def update_job_node(self, job_id: str, node_key: str, **fields: Any) -> None:
        allowed = {"status", "stale_reason", "error_message", "started_at", "finished_at"}
        keys = [key for key in fields if key in allowed]
        if not keys:
            return

        assignments = ", ".join(f"{key}=%s" for key in keys)
        params = [fields[key] for key in keys] + [job_id, node_key]
        with self.connect() as conn:
            conn.execute(
                f"update job_nodes set {assignments} where job_id=%s and node_key=%s",
                params,
            )

    def delete_job(self, job_id: str) -> None:
        with self.connect() as conn:
            job = conn.execute("select * from jobs where id=%s", (job_id,)).fetchone()
            if job is None:
                raise ValueError("Job not found")
            if job["status"] == "running":
                raise ValueError("Cannot delete a running job")
            conn.execute("delete from job_nodes where job_id=%s", (job_id,))
            conn.execute("delete from node_runs where job_id=%s", (job_id,))
            conn.execute("delete from jobs where id=%s", (job_id,))
