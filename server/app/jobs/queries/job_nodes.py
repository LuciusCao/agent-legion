from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from server.app.jobs.queries.job_node_lifecycle import JobNodeLifecycleQueriesMixin
from server.app.services.workflow_revisions import definition_from_job_snapshot
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
        workflow_definition_hash: str = "",
        workflow_definition_snapshot_json: str = "",
    ) -> dict[str, Any]:
        job_id = _job_id(workspace_id, workflow_key, source_id)
        storage_dir = self.jobs_dir / workspace_id / job_id
        storage_dir.mkdir(parents=True, exist_ok=True)
        data_dir = self.jobs_dir.parent

        with self.connect() as conn:
            existing = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
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
                  workflow_revision_id, workflow_definition_hash, workflow_definition_snapshot_json
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    make_data_relative(storage_dir, data_dir),
                    stem,
                    workflow_revision_id,
                    workflow_definition_hash,
                    workflow_definition_snapshot_json,
                ),
            )
            for node_key in node_keys:
                conn.execute(
                    """
                    insert or ignore into job_nodes(job_id, node_key, status, created_at)
                    values (?, ?, 'pending', current_timestamp)
                    """,
                    (job_id, node_key),
                )
            row = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
        return dict(row)

    def list_jobs(
        self,
        workflow_key: str | None = None,
        status: str | None = None,
        workspace_id: str | None = None,
        source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if workspace_id:
            clauses.append("workspace_id=?")
            params.append(workspace_id)
        if workflow_key:
            clauses.append("workflow_key=?")
            params.append(workflow_key)
        if status:
            clauses.append("status=?")
            params.append(status)
        if source_id:
            clauses.append("source_id=?")
            params.append(source_id)
        where = f" where {' and '.join(clauses)}" if clauses else ""
        with self._connect_read() as conn:
            rows = conn.execute(f"select * from jobs{where} order by created_at desc", params)
            return [dict(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def update_job_status(self, job_id: str, status: str, error_message: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update jobs
                set status=?, error_message=?, updated_at=current_timestamp
                where id=?
                """,
                (status, error_message, job_id),
            )

    def update_job_outcome(self, job_id: str, outcome: str) -> None:
        with self.connect() as conn:
            conn.execute("update jobs set outcome=? where id=?", (outcome, job_id))

    def list_job_nodes(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from job_nodes where job_id=? order by id", (job_id,))
            return [dict(row) for row in rows]

    def list_job_nodes_for_jobs(self, job_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        if not job_ids:
            return {}
        placeholders = ",".join("?" for _ in job_ids)
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
                "select * from job_nodes where job_id=? and node_key=?",
                (job_id, node_key),
            ).fetchone()
        return dict(row) if row else None

    def update_job_node(self, job_id: str, node_key: str, **fields: Any) -> None:
        allowed = {"status", "stale_reason", "error_message", "started_at", "finished_at"}
        keys = [key for key in fields if key in allowed]
        if not keys:
            return

        assignments = ", ".join(f"{key}=?" for key in keys)
        params = [fields[key] for key in keys] + [job_id, node_key]
        with self.connect() as conn:
            conn.execute(
                f"update job_nodes set {assignments} where job_id=? and node_key=?",
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
                where job_id=? and node_key=? and status in ('pending', 'ready', 'stale')
                """,
                (job_id, node_key),
            )
            if cursor.rowcount == 0:
                exists = conn.execute(
                    "select 1 from job_nodes where job_id=? and node_key=?",
                    (job_id, node_key),
                ).fetchone()
                if exists is None:
                    raise ValueError(f"Unknown job node: {job_id}.{node_key}")
                return None
            conn.execute(
                """
                update jobs
                set status='running', updated_at=current_timestamp
                where id=? and status != 'running'
                """,
                (job_id,),
            )
            cursor = conn.execute(
                """
                insert into node_runs(
                  job_id, node_key, status, command_json, log_path, run_dir, session_dir, skill_version
                )
                values (?, ?, 'running', ?, ?, ?, ?, ?)
                """,
                (job_id, node_key, command_json, log_path, run_dir, session_dir, skill_version),
            )
            row = conn.execute("select * from node_runs where id=?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def finish_node_run(self, run_id: int, status: str, exit_code: int, error_message: str) -> None:
        with self.connect() as conn:
            run = conn.execute("select * from node_runs where id=?", (run_id,)).fetchone()
            if run is None:
                return

            conn.execute(
                """
                update node_runs
                set status=?, exit_code=?, error_message=?, finished_at=current_timestamp
                where id=?
                """,
                (status, exit_code, error_message, run_id),
            )
            node_status = "completed" if status == "completed" else "failed"
            conn.execute(
                """
                update job_nodes
                set status=?,
                    error_message=?,
                    finished_at=current_timestamp
                where job_id=? and node_key=?
                """,
                (node_status, error_message, run["job_id"], run["node_key"]),
            )
            job = conn.execute("select * from jobs where id=?", (run["job_id"],)).fetchone()
            definition = definition_from_job_snapshot(dict(job)) if job is not None else None
            self._sync_job_status_after_node_run(conn, run, status, definition)

    def list_node_runs(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from node_runs where job_id=? order by id", (job_id,))
            return [dict(row) for row in rows]

    def get_node_run(self, job_id: str, run_id: int) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute(
                "select * from node_runs where job_id=? and id=?",
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
        clauses = ["jobs.workspace_id = ?"]
        params: list[Any] = [workspace_id]
        if status:
            clauses.append("node_runs.status = ?")
            params.append(status)
        if node_key:
            clauses.append("node_runs.node_key = ?")
            params.append(node_key)
        if job_id:
            clauses.append("node_runs.job_id = ?")
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
                limit ?
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
                where jobs.workspace_id = ?
                order by node_runs.started_at desc
                limit 1
                """,
                (workspace_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_job(self, job_id: str) -> None:
        with self.connect() as conn:
            job = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
            if job is None:
                raise ValueError("Job not found")
            if job["status"] == "running":
                raise ValueError("Cannot delete a running job")
            conn.execute("delete from job_nodes where job_id=?", (job_id,))
            conn.execute("delete from node_runs where job_id=?", (job_id,))
            conn.execute("delete from jobs where id=?", (job_id,))
