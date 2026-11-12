"""Empty-claim diagnostics: why a claim came back empty with stock present.

Split out of ``empty.py`` for the file-size budget: debounced WARNING plus
the persisted blocked-queue signal for the monitoring panel (issue #13).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from server.app.db.dialect import ConnectSource
from server.app.db.transaction import write_transaction

logger = logging.getLogger(__name__)

_QUEUE_HEAD_LIMIT = 5


def read_queue_head(conn: Any) -> list[dict[str, str]]:
    """Sample the blocked-queue head on the caller's read connection.

    Conn-first (#1239): the probe caller already holds a read checkout, so
    this read must ride it instead of opening a second pool checkout.
    """
    rows = conn.execute(
        "select execution_id, workspace_id, job_id, node_key, queued_at"
        " from agent_execution_requests where state='queued'"
        " order by queued_at, execution_id limit %s",
        (_QUEUE_HEAD_LIMIT,),
    ).fetchall()
    return [
        {
            "execution_id": str(row["execution_id"]),
            "workspace_id": str(row["workspace_id"]),
            "job_id": str(row["job_id"]),
            "node_key": str(row["node_key"]),
            "queued_at": str(row["queued_at"]),
        }
        for row in rows
    ]


def log_blocked_queue(
    dsn: ConnectSource, skip_reasons: Mapping[str, int], head: list[dict[str, str]]
) -> None:
    """Log the blocked-queue signal and persist it for the ops summary.

    Owns its write checkout, so call it only after the probe's read
    connection has been released: persisting while the read checkout is
    still held is a nested pool checkout (#1239) — at pool size 1 the write
    times out after 10s and PoolTimeout propagates into the Worker claim
    response path.
    """
    logger.warning(
        "agent claim came back empty with queued requests present (blocked queue):"
        " skip_reasons=%s queue_head=%s",
        dict(skip_reasons),
        head,
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
