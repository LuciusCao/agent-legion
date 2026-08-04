"""Empty-claim diagnostics: why a claim came back empty with stock present.

Split out of ``empty.py`` for the file-size budget. The claim pass counts
why each candidate lost (see ``claim_scan.py``); when the queue still holds
queued rows yet no Worker could claim anything, that is a blocked queue —
the load-drop-with-no-cause failure mode from the 2026-08-01 incident — and
worth a debounced WARNING carrying the reason histogram and the queue head.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

_QUEUE_HEAD_LIMIT = 5


def log_blocked_queue(conn: Any, skip_reasons: Mapping[str, int]) -> None:
    """Log the blocked-queue signal: skip histogram plus the queue head."""
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
