from __future__ import annotations

import hashlib
import json
from typing import Any

from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.queries.batch_queue import RunQueueQueriesMixin
from server.app.jobs.queries.batch_queue_sql import RUN_UPSERT_CONFLICT


class RunQueriesMixin(RunQueueQueriesMixin, JobQueriesBase):
    def create_run(
        self,
        workflow_key: str,
        source_kind: str,
        digest_payload: dict[str, Any],
        workspace_id: str,
        status: str = "created",
        frozen_pins: dict[str, Any] | None = None,
        queue_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert (or upsert) a run; ``digest_payload`` only derives the id.

        The deterministic id keeps re-submitting identical intake input a
        no-op (the upsert clause requeues only failed runs). Frozen pins
        (node_code_versions / agent_versions / quality_replay) persist on the
        run row; per-job frozen configs/inputs land on the jobs rows created
        by the caller (RUN-FREEZE-001). ``queue_payload`` is the async intake
        working state and stays empty for synchronous runs.
        """
        payload_json = json.dumps(digest_payload, ensure_ascii=False, sort_keys=True)
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:16]
        run_id = f"{workspace_id}_{workflow_key}_{source_kind}_{payload_digest}"
        pins_json = json.dumps(frozen_pins or {}, ensure_ascii=False, sort_keys=True)
        queue_json = (
            json.dumps(queue_payload, ensure_ascii=False, sort_keys=True)
            if queue_payload is not None
            else ""
        )
        with self.connect() as conn:
            conn.execute(
                f"""
                insert into runs(
                  id, workspace_id, workflow_key, source_kind, status,
                  frozen_pins_json, queue_payload_json
                ) values (%s, %s, %s, %s, %s, %s, %s)
                {RUN_UPSERT_CONFLICT}
                """,
                (run_id, workspace_id, workflow_key, source_kind, status, pins_json, queue_json),
            )
            row = conn.execute("select * from runs where id=%s", (run_id,)).fetchone()
        if row is None:
            raise RuntimeError("run upsert did not return a row")
        return dict(row)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        if not run_id:
            return None
        with self._connect_read() as conn:
            row = conn.execute("select * from runs where id=%s", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, workspace_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect_read() as conn:
            rows = conn.execute(
                "select * from runs where workspace_id=%s"
                " order by created_at desc, id desc limit %s",
                (workspace_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def count_jobs_by_status_in_run(self, run_id: str) -> dict[str, int]:
        with self._connect_read() as conn:
            rows = conn.execute(
                "select status, count(*) as count from jobs where run_id=%s group by status",
                (run_id,),
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}
