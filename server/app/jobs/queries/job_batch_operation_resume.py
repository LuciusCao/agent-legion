"""Guarded stream/output snapshots for quickly resumed batch operations (#1397)."""

from __future__ import annotations

import json
from typing import Any

from server.app.jobs.queries.connection import ConnectionQueriesMixin


class JobBatchOperationResumeQueriesMixin(ConnectionQueriesMixin):
    def preserve_resumed_job_batch_operation_snapshot(
        self,
        op_id: str,
        *,
        expected_next_index: int,
        target_spec: dict[str, Any] | None,
        items_total: int | None,
        output: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Keep chunk identity and completion output stable for resumed replay."""
        spec_json = (
            json.dumps(target_spec, ensure_ascii=False, sort_keys=True)
            if target_spec is not None
            else None
        )
        output_json = json.dumps(output or {}, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            row = conn.execute(
                """
                update job_batch_operations
                set target_spec_json=coalesce(%s, target_spec_json),
                    items_total=coalesce(%s, items_total),
                    output_json=case when %s then %s else output_json end,
                    updated_at=current_timestamp
                where id=%s and status='pending' and next_index=%s
                returning *
                """,
                (
                    spec_json,
                    items_total,
                    output is not None,
                    output_json,
                    op_id,
                    expected_next_index,
                ),
            ).fetchone()
        return dict(row) if row else None
