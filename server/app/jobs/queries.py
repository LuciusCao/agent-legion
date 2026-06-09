from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from server.app.db.schema import init_db


def _safe_identifier(value: str, fallback: str) -> str:
    safe_value = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip()).strip("_")
    return safe_value or fallback


def _job_id(workspace_id: str, pipeline_key: str, source_id: str) -> str:
    safe_source_id = source_id.strip().replace("/", "_")
    return f"{workspace_id}_{pipeline_key}_{safe_source_id}"


def _workspace_id(name: str) -> str:
    return _safe_identifier(name.lower(), "workspace")


def _decode_json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _workspace_record(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["cms_config"] = _decode_json_object(record.get("cms_config_json"))
    record["resource_config"] = _decode_json_object(record.get("resource_config_json"))
    record["intake_config"] = _decode_json_object(record.get("intake_config_json"))
    return record


class JobQueries:
    def __init__(self, path: Path, jobs_dir: Path):
        self.path = path
        self.jobs_dir = jobs_dir
        init_db(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _connect_read(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def create_workspace(
        self,
        name: str,
        default_pipeline_key: str = "question_content",
        cms_config: dict[str, Any] | None = None,
        resource_config: dict[str, Any] | None = None,
        default_entity: str = "question",
        intake_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Workspace name is required")
        cms_config_json = json.dumps(cms_config or {}, ensure_ascii=False, sort_keys=True)
        resource_config_json = json.dumps(
            resource_config or {},
            ensure_ascii=False,
            sort_keys=True,
        )
        clean_entity = (default_entity or "question").strip() or "question"
        intake_config_json = json.dumps(intake_config or {}, ensure_ascii=False, sort_keys=True)

        base_id = _workspace_id(clean_name)
        with self.connect() as conn:
            workspace_id = base_id
            suffix = 2
            while conn.execute("select 1 from workspaces where id=?", (workspace_id,)).fetchone():
                workspace_id = f"{base_id}_{suffix}"
                suffix += 1

            conn.execute(
                """
                insert into workspaces(
                  id, name, default_pipeline_key, cms_config_json, resource_config_json,
                  default_entity, intake_config_json
                )
                values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    clean_name,
                    default_pipeline_key,
                    cms_config_json,
                    resource_config_json,
                    clean_entity,
                    intake_config_json,
                ),
            )
            row = conn.execute("select * from workspaces where id=?", (workspace_id,)).fetchone()
        return _workspace_record(row)

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from workspaces order by created_at, id")
            return [_workspace_record(row) for row in rows]

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        with self._connect_read() as conn:
            row = conn.execute("select * from workspaces where id=?", (workspace_id,)).fetchone()
        return _workspace_record(row) if row else None

    def update_workspace(
        self,
        workspace_id: str,
        *,
        name: str | None = None,
        default_pipeline_key: str | None = None,
        cms_config: dict[str, Any] | None = None,
        resource_config: dict[str, Any] | None = None,
        default_entity: str | None = None,
        intake_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise ValueError("Workspace name is required")
            fields["name"] = clean_name
        if default_pipeline_key is not None:
            fields["default_pipeline_key"] = default_pipeline_key
        if cms_config is not None:
            fields["cms_config_json"] = json.dumps(
                cms_config,
                ensure_ascii=False,
                sort_keys=True,
            )
        if resource_config is not None:
            fields["resource_config_json"] = json.dumps(
                resource_config,
                ensure_ascii=False,
                sort_keys=True,
            )
        if default_entity is not None:
            clean_entity = (default_entity or "question").strip() or "question"
            fields["default_entity"] = clean_entity
        if intake_config is not None:
            fields["intake_config_json"] = json.dumps(
                intake_config,
                ensure_ascii=False,
                sort_keys=True,
            )
        if not fields:
            workspace = self.get_workspace(workspace_id)
            if workspace is None:
                raise ValueError("Workspace not found")
            return workspace

        assignments = ", ".join(f"{key}=?" for key in fields)
        params = list(fields.values()) + [workspace_id]
        with self.connect() as conn:
            cursor = conn.execute(
                f"""
                update workspaces
                set {assignments}, updated_at=current_timestamp
                where id=?
                """,
                params,
            )
            if cursor.rowcount == 0:
                raise ValueError("Workspace not found")
            row = conn.execute("select * from workspaces where id=?", (workspace_id,)).fetchone()
        return _workspace_record(row)

    def create_batch(
        self,
        pipeline_key: str,
        source_kind: str,
        source_payload: dict[str, Any],
        workspace_id: str = "default",
    ) -> dict[str, Any]:
        payload_json = json.dumps(source_payload, ensure_ascii=False, sort_keys=True)
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:16]
        batch_id = f"{workspace_id}_{pipeline_key}_{source_kind}_{payload_digest}"
        with self.connect() as conn:
            conn.execute(
                """
                insert into job_batches(id, workspace_id, pipeline_key, source_kind, source_payload_json)
                values (?, ?, ?, ?, ?)
                on conflict(id) do update set source_payload_json=excluded.source_payload_json
                """,
                (batch_id, workspace_id, pipeline_key, source_kind, payload_json),
            )
            row = conn.execute("select * from job_batches where id=?", (batch_id,)).fetchone()
        return dict(row)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        if not batch_id:
            return None
        with self._connect_read() as conn:
            row = conn.execute("select * from job_batches where id=?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def create_job(
        self,
        pipeline_key: str,
        source_type: str,
        source_id: str,
        batch_id: str,
        title: str,
        node_keys: list[str],
        workspace_id: str = "default",
    ) -> dict[str, Any]:
        job_id = _job_id(workspace_id, pipeline_key, source_id)
        storage_dir = self.jobs_dir / workspace_id / job_id
        storage_dir.mkdir(parents=True, exist_ok=True)

        with self.connect() as conn:
            existing = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
            if existing is not None and (
                existing["workspace_id"] != workspace_id
                or existing["pipeline_key"] != pipeline_key
                or existing["source_type"] != source_type
                or existing["source_id"] != source_id
            ):
                raise ValueError(f"Job identity collision for {job_id}")
            conn.execute(
                """
                insert into jobs(
                  id, workspace_id, pipeline_key, source_type, source_id, batch_id, title, storage_dir
                )
                values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(id) do update set
                  title=excluded.title,
                  batch_id=excluded.batch_id,
                  updated_at=current_timestamp
                """,
                (
                    job_id,
                    workspace_id,
                    pipeline_key,
                    source_type,
                    source_id,
                    batch_id,
                    title,
                    str(storage_dir),
                ),
            )
            for node_key in node_keys:
                conn.execute(
                    """
                    insert or ignore into job_nodes(job_id, node_key, status)
                    values (?, ?, 'pending')
                    """,
                    (job_id, node_key),
                )
            row = conn.execute("select * from jobs where id=?", (job_id,)).fetchone()
        return dict(row)

    def list_jobs(
        self,
        pipeline_key: str | None = None,
        status: str | None = None,
        workspace_id: str | None = "default",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if workspace_id:
            clauses.append("workspace_id=?")
            params.append(workspace_id)
        if pipeline_key:
            clauses.append("pipeline_key=?")
            params.append(pipeline_key)
        if status:
            clauses.append("status=?")
            params.append(status)
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

    def list_job_nodes(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from job_nodes where job_id=? order by id", (job_id,))
            return [dict(row) for row in rows]

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

    def mark_node_for_rerun(self, job_id: str, node_key: str, downstream: list[str]) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update job_nodes
                set status='pending',
                    stale_reason='',
                    error_message='',
                    started_at=null,
                    finished_at=null
                where job_id=? and node_key=?
                """,
                (job_id, node_key),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Unknown job node: {job_id}.{node_key}")
            for downstream_key in downstream:
                conn.execute(
                    """
                    update job_nodes
                    set status='stale',
                        stale_reason=?,
                        error_message=''
                    where job_id=? and node_key=?
                    """,
                    (f"upstream {node_key} rerun", job_id, downstream_key),
                )
            conn.execute(
                """
                update jobs
                set status='queued',
                    error_message='',
                    updated_at=current_timestamp
                where id=?
                """,
                (job_id,),
            )

    def start_node_run(
        self, job_id: str, node_key: str, command: Sequence[str], log_path: str
    ) -> dict[str, Any]:
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
                where job_id=? and node_key=?
                """,
                (job_id, node_key),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Unknown job node: {job_id}.{node_key}")
            cursor = conn.execute(
                """
                insert into node_runs(job_id, node_key, status, command_json, log_path)
                values (?, ?, 'running', ?, ?)
                """,
                (job_id, node_key, command_json, log_path),
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

    def list_node_runs(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute("select * from node_runs where job_id=? order by id", (job_id,))
            return [dict(row) for row in rows]

    def count_jobs_by_status(self, workspace_id: str) -> dict[str, int]:
        with self._connect_read() as conn:
            rows = conn.execute(
                "select status, count(*) as cnt from jobs where workspace_id = ? group by status",
                (workspace_id,),
            )
            return {row["status"]: row["cnt"] for row in rows}

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

    def delete_workspace(self, workspace_id: str) -> None:
        if workspace_id == "default":
            raise ValueError("Cannot delete the default workspace")
        with self.connect() as conn:
            running = conn.execute(
                "select 1 from jobs where workspace_id = ? and status = ?",
                (workspace_id, "running"),
            ).fetchone()
            if running is not None:
                raise ValueError("Cannot delete workspace with running jobs")
            conn.execute(
                "delete from job_nodes where job_id in (select id from jobs where workspace_id = ?)",
                (workspace_id,),
            )
            conn.execute(
                "delete from node_runs where job_id in (select id from jobs where workspace_id = ?)",
                (workspace_id,),
            )
            conn.execute(
                "delete from job_batches where workspace_id = ?",
                (workspace_id,),
            )
            conn.execute(
                "delete from jobs where workspace_id = ?",
                (workspace_id,),
            )
            cursor = conn.execute(
                "delete from workspaces where id = ?",
                (workspace_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError("Workspace not found")
