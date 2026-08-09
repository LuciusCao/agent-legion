from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from server.app.jobs.queries.job_node_lifecycle import JobNodeLifecycleQueriesMixin
from server.app.jobs.storage_layout import job_storage_dir
from server.app.services.workflow_revision_format import definition_from_job_snapshot
from server.app.storage_paths import make_data_relative


def _job_id(workspace_id: str, workflow_key: str, source_id: str) -> str:
    safe_source_id = source_id.strip().replace("/", "_")
    return f"{workspace_id}_{workflow_key}_{safe_source_id}"


class JobNodeQueriesMixin(JobNodeLifecycleQueriesMixin):
    jobs_dir: Path

    def create_job(
        self,
        workflow_key: str,
        source_type: str,
        source_id: str,
        batch_id: str,
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
                or existing["workflow_key"] != workflow_key
                or existing["source_type"] != source_type
                or existing["source_id"] != source_id
            ):
                raise ValueError(f"Job identity collision for {job_id}")
            conn.execute(
                """
                insert into jobs(
                  id, workspace_id, workflow_key, source_type, source_id, batch_id, title, storage_dir, stem,
                  workflow_revision_id, workflow_version, workflow_definition_hash, workflow_definition_snapshot_json
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict(id) do update set
                  title=excluded.title,
                  stem=excluded.stem,
                  batch_id=excluded.batch_id,
                  updated_at=current_timestamp
                """,
                (
                    job_id,
                    workspace_id,
                    workflow_key,
                    source_type,
                    source_id,
                    batch_id,
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
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        for col, val in (
            ("workspace_id", workspace_id),
            ("workflow_key", workflow_key),
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
        with self._connect_read() as conn:
            rows = conn.execute(f"select * from jobs{where} order by created_at desc", params)
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

    def start_node_run(
        self,
        job_id: str,
        node_key: str,
        command: Sequence[str],
        log_path: str,
        *,
        run_dir: str = "",
        session_dir: str = "",
        skill_version: str = "",
    ) -> dict[str, Any] | None:
        command_json = json.dumps(list(command))
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update job_nodes
                set status='running',
                    stale_reason='',
                    started_at=current_timestamp,
                    finished_at=null,
                    error_message=''
                where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale')
                """,
                (job_id, node_key),
            )
            if cursor.rowcount == 0:
                exists = conn.execute(
                    "select 1 from job_nodes where job_id=%s and node_key=%s",
                    (job_id, node_key),
                ).fetchone()
                if exists is None:
                    raise ValueError(f"Unknown job node: {job_id}.{node_key}")
                return None
            conn.execute(
                """
                update jobs
                set status='running', updated_at=current_timestamp
                where id=%s and status != 'running'
                """,
                (job_id,),
            )
            cursor = conn.execute(
                """
                insert into node_runs(
                  job_id, node_key, status, command_json, log_path, run_dir, session_dir, skill_version
                )
                values (%s, %s, 'running', %s, %s, %s, %s, %s)
                returning *
                """,
                (job_id, node_key, command_json, log_path, run_dir, session_dir, skill_version),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("node run insert did not return a row")
        return dict(row)

    def finish_node_run(self, run_id: int, status: str, exit_code: int, error_message: str) -> None:
        with self.connect() as conn:
            run = conn.execute("select * from node_runs where id=%s", (run_id,)).fetchone()
            if run is None:
                return

            conn.execute(
                """
                update node_runs
                set status=%s, exit_code=%s, error_message=%s, finished_at=current_timestamp
                where id=%s
                """,
                (status, exit_code, error_message, run_id),
            )
            node_status = "completed" if status == "completed" else "failed"
            conn.execute(
                """
                update job_nodes
                set status=%s,
                    error_message=%s,
                    finished_at=current_timestamp
                where job_id=%s and node_key=%s
                """,
                (node_status, error_message, run["job_id"], run["node_key"]),
            )
            job = conn.execute("select * from jobs where id=%s", (run["job_id"],)).fetchone()
            definition = definition_from_job_snapshot(dict(job)) if job is not None else None
            self._sync_job_status_after_node_run(conn, run, status, definition)

    def list_node_runs(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from node_runs where job_id=%s order by id", (job_id,))
            return [dict(row) for row in rows]

    def get_node_run(self, job_id: str, run_id: int) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                "select * from node_runs where job_id=%s and id=%s",
                (job_id, run_id),
            ).fetchone()
        return dict(row) if row else None

    def list_workspace_node_runs(
        self,
        workspace_id: str,
        *,
        status: str | None = None,
        node_key: str | None = None,
        job_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["jobs.workspace_id = %s"]
        params: list[Any] = [workspace_id]
        if status:
            clauses.append("node_runs.status = %s")
            params.append(status)
        if node_key:
            clauses.append("node_runs.node_key = %s")
            params.append(node_key)
        if job_id:
            clauses.append("node_runs.job_id = %s")
            params.append(job_id)
        params.append(max(1, min(limit, 500)))
        where = " and ".join(clauses)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"""
                select
                  node_runs.*,
                  jobs.workspace_id,
                  jobs.title as job_title,
                  jobs.source_id,
                  jobs.source_type,
                  jobs.workflow_key
                from node_runs
                join jobs on jobs.id = node_runs.job_id
                where {where}
                order by node_runs.started_at desc, node_runs.id desc
                limit %s
                """,
                params,
            )
            return [dict(row) for row in rows]

    def get_latest_node_run_for_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                """
                select node_runs.*
                from node_runs
                join jobs on jobs.id = node_runs.job_id
                where jobs.workspace_id = %s
                order by node_runs.started_at desc
                limit 1
                """,
                (workspace_id,),
            ).fetchone()
        return dict(row) if row else None

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
