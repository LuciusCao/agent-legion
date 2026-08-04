"""Empty-claim diagnostics: why a claim came back empty with stock present.

Split out of ``empty.py`` for the file-size budget: debounced WARNING plus
the persisted blocked-queue signal for the monitoring panel (issue #13).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from server.app.db.connection import DatabaseDsn
from server.app.db.transaction import write_transaction

logger = logging.getLogger(__name__)

_QUEUE_HEAD_LIMIT = 5


def log_blocked_queue(dsn: DatabaseDsn, conn: Any, skip_reasons: Mapping[str, int]) -> None:
    """Log the blocked-queue signal and persist it for the ops summary."""
    head = conn.execute(
        "select execution_id, workspace_id, job_id, node_key, queued_at"
        " from agent_execution_requests where state='queued'"
        " order by queued_at, execution_id limit %s",
        (_QUEUE_HEAD_LIMIT,),
    ).fetchall()
    logger.warning(
        "agent claim came back empty with queued requests present (blocked queue):"
        " skip_reasons=%s queue_head=%s",
        dict(skip_reasons),
        [
            {
                "execution_id": str(row["execution_id"]),
                "workspace_id": str(row["workspace_id"]),
                "job_id": str(row["job_id"]),
                "node_key": str(row["node_key"]),
                "queued_at": str(row["queued_at"]),
            }
            for row in head
        ],
    )
    with write_transaction(dsn) as write_conn:
        write_conn.execute(
            "insert into agent_queue_signals(id, kind, reasons_json, updated_at)"
            " values (1, 'blocked', %s, current_timestamp)"
            " on conflict(id) do update set"
            " kind='blocked', reasons_json=excluded.reasons_json,"
            " updated_at=current_timestamp",
            (json.dumps(dict(skip_reasons), sort_keys=True),),
        )
