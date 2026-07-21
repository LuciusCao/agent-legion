from __future__ import annotations

import hashlib
import json
from typing import Any

from server.app.jobs.queries.base import JobQueriesBase
from server.app.jobs.queries.batch_queue import BatchQueueQueriesMixin
from server.app.jobs.queries.batch_queue_sql import BATCH_UPSERT_CONFLICT


class BatchQueriesMixin(BatchQueueQueriesMixin, JobQueriesBase):
    def create_batch(
        self,
        workflow_key: str,
        source_kind: str,
        source_payload: dict[str, Any],
        workspace_id: str,
        status: str = "created",
    ) -> dict[str, Any]:
        payload_json = json.dumps(source_payload, ensure_ascii=False, sort_keys=True)
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:16]
        batch_id = f"{workspace_id}_{workflow_key}_{source_kind}_{payload_digest}"
        with self.connect() as conn:
            conn.execute(
                f"""
                insert into job_batches(
                  id, workspace_id, workflow_key, source_kind, source_payload_json, status
                ) values (?, ?, ?, ?, ?, ?)
                {BATCH_UPSERT_CONFLICT}
                """,
                (batch_id, workspace_id, workflow_key, source_kind, payload_json, status),
            )
            row = conn.execute("select * from job_batches where id=?", (batch_id,)).fetchone()
        if row is None:
            raise RuntimeError("job batch upsert did not return a row")
        return dict(row)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        if not batch_id:
            return None
        with self._connect_read() as conn:
            row = conn.execute("select * from job_batches where id=?", (batch_id,)).fetchone()
        return dict(row) if row else None
