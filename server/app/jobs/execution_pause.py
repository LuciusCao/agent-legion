"""Guarded execution_paused flag mutations for operator pause/resume.

Consumed by ``JobQueries``; relies on ``self.connect()`` provided by the
combined class (same pattern as ``JobExecutionControlMixin``).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from server.app.db.connection import DatabaseConnection


class _JobQueries(Protocol):
    """Minimal shape of ``JobQueries`` required by this mixin."""

    def connect(self) -> AbstractContextManager[DatabaseConnection]: ...


class JobExecutionPauseMixin:
    def mark_execution_paused(self: _JobQueries, job_id: str, reason: str) -> bool:
        """Pause a non-terminal, not-yet-paused job; False when the guard rejects.

        The guard predicates live in the UPDATE itself so a concurrent pause,
        completion or run-to target reach can never be overwritten.
        """
        clean_reason = (reason or "").strip()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set execution_paused=1,
                    pause_reason=%s,
                    updated_at=current_timestamp
                where id=%s
                  and execution_paused=0
                  and status not in ('completed', 'failed')
                """,
                (clean_reason, job_id),
            )
            return cursor.rowcount > 0

    def clear_execution_paused(self: _JobQueries, job_id: str) -> bool:
        """Resume an operator-paused job; False when the guard rejects.

        Run-to pauses (``pause_reason='target_reached'``) are left to the
        continue flow. A job already projected to ``status='paused'`` (no
        running nodes left) returns to ``queued`` so dispatch re-enables it.
        """
        with self.connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set execution_paused=0,
                    pause_reason='',
                    status=case when status='paused' then 'queued' else status end,
                    updated_at=current_timestamp
                where id=%s
                  and execution_paused=1
                  and pause_reason <> 'target_reached'
                """,
                (job_id,),
            )
            return cursor.rowcount > 0
