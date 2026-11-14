from __future__ import annotations

import logging
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors._execution_control_guard import execution_control_rejects_node
from server.app.executors._lease_control import (
    TERMINAL_JOB_STATUSES,
    _read_job_execution_control,
    lock_job_mutation_and_read_generation,
)

logger = logging.getLogger(__name__)


def lock_and_validate_approval_park(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    *,
    execution_generation: int,
    control_snapshot: dict[str, Any] | None,
    allowed_node_keys: frozenset[str] | None,
) -> dict[str, Any] | None:
    current_generation = lock_job_mutation_and_read_generation(conn, job_id)
    if current_generation != execution_generation:
        logger.info(
            "approval park skipped (stale generation): job=%s node=%s expected=%s current=%s",
            job_id,
            node_key,
            execution_generation,
            current_generation,
        )
        return None
    current = _read_job_execution_control(conn, job_id)
    if current["status"] in TERMINAL_JOB_STATUSES:
        return None
    snapshot = control_snapshot or {}
    if execution_control_rejects_node(
        node_key=node_key,
        execution_mode=str(snapshot.get("execution_mode", "full")),
        target_node_key=snapshot.get("target_node_key"),
        allowed_node_keys=allowed_node_keys,
        current_control=current,
    ):
        return None
    return current
