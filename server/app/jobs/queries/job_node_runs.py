"""node_runs lifecycle and listing queries.

Split from ``job_nodes.py`` when the skill-key column (schema v75, #410) grew
the workspace listing past that module's budget — the run-record family
(start/finish/list/get) moves as one cohesive unit, same mixin pattern as
``job_node_lifecycle.py``.
"""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.job_node_lifecycle import JobNodeLifecycleQueriesMixin
from server.app.workflows.revision_format import definition_from_job_snapshot


class JobNodeRunQueriesMixin(JobNodeLifecycleQueriesMixin):
    def start_node_run(
        self,
        job_id: str,
        node_key: str,
        command: tuple[str, ...] | list[str],
        log_path: str,
        *,
        run_dir: str = "",
        session_dir: str = "",
        skill_version: str = "",
        skill: str = "",
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
                  job_id, node_key, status, command_json, log_path, run_dir, session_dir,
                  skill_version, skill
                )
                values (%s, %s, 'running', %s, %s, %s, %s, %s, %s)
                returning *
                """,
                (
                    job_id,
                    node_key,
                    command_json,
                    log_path,
                    run_dir,
                    session_dir,
                    skill_version,
                    skill,
                ),
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
        skill: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        # #410 review: the endpoint response is NodeRunResponse (was a loose
        # dict list, codex P1 on #427), so project node_runs columns only —
        # pydantic v2 default is extra='ignore': join fields the contract
        # does not declare are silently stripped, not rejected (independent
        # review P3 on #427). The real backstops are the generated frontend
        # types and the schema assertions in test_jobs_route_contracts.
        # #410 codex four-pass P1: skill filter (schema v75) — a node rebound
        # from skill-a to skill-b must not echo a's run as b's execution.
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
        if skill:
            clauses.append("node_runs.skill = %s")
            params.append(skill)
        params.append(max(1, min(limit, 500)))
        where = " and ".join(clauses)
        with self._connect_read() as conn:
            rows = conn.execute(
                f"""
                select node_runs.*
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
