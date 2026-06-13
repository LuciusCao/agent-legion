from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from typing import Any, Protocol


class _JobQueries(Protocol):
    """Minimal shape of ``JobQueries`` required by this mixin."""

    def connect(self) -> AbstractContextManager[sqlite3.Connection]: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...


class JobExecutionControlMixin:
    """Persistence helpers for workspace job execution-control state.

    This mixin is consumed by ``JobQueries``; it relies on ``self.connect()``
    and ``self.get_job()`` provided by the combined class.
    """

    def set_job_execution_target(self: _JobQueries, job_id: str, target_node_key: str) -> None:
        target = (target_node_key or "").strip()
        if not target:
            raise ValueError("target_node_key is required")
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set execution_mode='targeted',
                    target_node_key=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (target, job_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Job not found")

    def clear_job_execution_target(self: _JobQueries, job_id: str) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set execution_mode='full',
                    target_node_key=null,
                    updated_at=current_timestamp
                where id=?
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
                    pause_reason=?,
                    updated_at=current_timestamp
                where id=?
                """,
                (clean_reason, job_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("Job not found")

    def resume_job(self: _JobQueries, job_id: str) -> None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set execution_paused=0,
                    pause_reason='',
                    updated_at=current_timestamp
                where id=?
                """,
                (job_id,),
            )
            if cursor.rowcount == 0:
                raise ValueError("Job not found")

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
