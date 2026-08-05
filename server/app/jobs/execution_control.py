from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any, Protocol

from server.app.db.connection import DatabaseConnection
from server.app.jobs.atomic_mutations import resume_job as resume_job_mutation


class _JobQueries(Protocol):
    """Minimal shape of ``JobQueries`` required by this mixin."""

    def connect(self) -> AbstractContextManager[DatabaseConnection]: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    def set_job_execution_mode(
        self,
        job_id: str,
        mode: str,
        *,
        target_node_key: str | None = None,
    ) -> None: ...


_VALID_EXECUTION_MODES = {"full", "until_node"}


def _validate_execution_mode(mode: str) -> str:
    clean = (mode or "").strip()
    if clean not in _VALID_EXECUTION_MODES:
        raise ValueError(f"invalid execution_mode: {mode!r}")
    return clean


class JobExecutionControlMixin:
    """Persistence helpers for workspace job execution-control state.

    This mixin is consumed by ``JobQueries``; it relies on ``self.connect()``
    and ``self.get_job()`` provided by the combined class.
    """

    def set_job_execution_mode(
        self: _JobQueries,
        job_id: str,
        mode: str,
        *,
        target_node_key: str | None = None,
    ) -> None:
        clean_mode = _validate_execution_mode(mode)
        target = (target_node_key or "").strip() or None
        if clean_mode == "until_node" and not target:
            raise ValueError("target_node_key is required for until_node mode")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set execution_mode=%s,
                    target_node_key=%s,
                    updated_at=current_timestamp
                where id=%s
                """,
                (clean_mode, target, job_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Job not found")

    def set_job_execution_target(self: _JobQueries, job_id: str, target_node_key: str) -> None:
        target = (target_node_key or "").strip()
        if not target:
            raise ValueError("target_node_key is required")
        with self.connect() as conn:
            job = conn.execute(
                "select workflow_key from jobs where id=%s",
                (job_id,),
            ).fetchone()
            if job is None:
                raise ValueError("Job not found")
            node = conn.execute(
                "select 1 from job_nodes where job_id=%s and node_key=%s",
                (job_id, target),
            ).fetchone()
            if node is None:
                raise ValueError(f"Unknown target node {target!r} for job {job_id}")
        self.set_job_execution_mode(job_id, "until_node", target_node_key=target)

    def clear_job_execution_target(self: _JobQueries, job_id: str) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set execution_mode='full',
                    target_node_key=null,
                    updated_at=current_timestamp
                where id=%s
                """,
                (job_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError("Job not found")

    def pause_job(self: _JobQueries, job_id: str, reason: str) -> None:
        clean_reason = (reason or "").strip()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set execution_paused=1,
                    pause_reason=%s,
                    updated_at=current_timestamp
                where id=%s
                """,
                (clean_reason, job_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Job not found")

    def resume_job(self: _JobQueries, job_id: str) -> None:
        with self.connect() as conn:
            resume_job_mutation(conn, job_id)

    def get_job_execution_control(self: _JobQueries, job_id: str) -> dict[str, Any] | None:
        row = self.get_job(job_id)
        if row is None:
            return None
        return {
            "job_id": row["id"],
            "execution_mode": row["execution_mode"],
            "target_node_key": row["target_node_key"],
            "execution_paused": bool(row["execution_paused"]),
            "pause_reason": row["pause_reason"],
        }
