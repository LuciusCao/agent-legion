from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


class BatchQueriesMixin:
    def create_batch(
        self,
        workflow_key: str,
        source_kind: str,
        source_payload: dict[str, Any],
        workspace_id: str = "default",
    ) -> dict[str, Any]:
        payload_json = json.dumps(source_payload, ensure_ascii=False, sort_keys=True)
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:16]
        batch_id = f"{workspace_id}_{workflow_key}_{source_kind}_{payload_digest}"
        with self.connect() as conn:
            conn.execute(
                """
                insert into job_batches(id, workspace_id, workflow_key, source_kind, source_payload_json)
                values (?, ?, ?, ?, ?)
                on conflict(id) do update set source_payload_json=excluded.source_payload_json
                """,
                (batch_id, workspace_id, workflow_key, source_kind, payload_json),
            )
            row = conn.execute("select * from job_batches where id=?", (batch_id,)).fetchone()
        return dict(row)

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        if not batch_id:
            return None
        with self._connect_read() as conn:
            row = conn.execute("select * from job_batches where id=?", (batch_id,)).fetchone()
        return dict(row) if row else None
