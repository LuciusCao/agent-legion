from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.executors._lease_control import (
    _execution_control_rejects_claim,
    _read_job_execution_control,
)
from server.app.executors._lease_transactions import _sqlite_timestamp
from server.app.executors._path_canonicalization import canonicalize_data_path
from server.app.executors.models import ClaimedExecution, LeaseClaimRequest


def claim_lease(
    conn: sqlite3.Connection, request: LeaseClaimRequest, data_dir: Path | None = None
) -> ClaimedExecution | None:
    """Attempt to claim a node run under capacity limits.

    Must run inside an active transaction. Returns None when capacity is
    exhausted or the node is not claimable; the caller is responsible for
    rolling back. Raises ValueError for configuration mismatches.
    """
    lease_id = str(uuid.uuid4())
    execution_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    current_control = _read_job_execution_control(conn, request.job_id)
    if _execution_control_rejects_claim(request, current_control):
        return None

    allocation = conn.execute(
        """
        select concurrency_limit
        from workspace_executor_allocations
        where workspace_id=? and executor_id=?
        """,
        (request.workspace_id, request.executor_id),
    ).fetchone()
    if allocation is None:
        raise ValueError(
            f"No allocation for executor {request.executor_id} in workspace {request.workspace_id}"
        )
    workspace_limit = allocation["concurrency_limit"]
    if workspace_limit <= 0:
        raise ValueError(
            f"Invalid workspace allocation limit {workspace_limit} for {request.executor_id}"
        )

    binding = conn.execute(
        """
        select executor_id
        from workspace_node_bindings
        where workspace_id=? and workflow_key=? and node_key=?
        """,
        (request.workspace_id, request.workflow_key, request.node_key),
    ).fetchone()
    if binding is None:
        raise ValueError(
            f"No binding for node {request.node_key} in {request.workspace_id}/{request.workflow_key}"
        )
    if binding["executor_id"] != request.executor_id:
        raise ValueError(
            f"Node {request.node_key} is bound to {binding['executor_id']}, not {request.executor_id}"
        )

    if request.local_node_limit is not None:
        limit_row = conn.execute(
            """
            select concurrency_limit
            from workspace_node_limits
            where workspace_id=? and workflow_key=? and node_key=?
            """,
            (request.workspace_id, request.workflow_key, request.node_key),
        ).fetchone()
        if limit_row is None:
            raise ValueError(
                f"No local node limit for {request.node_key} in {request.workspace_id}/{request.workflow_key}"
            )
        if limit_row["concurrency_limit"] != request.local_node_limit:
            raise ValueError(
                f"Local node limit mismatch for {request.node_key}: "
                f"persisted {limit_row['concurrency_limit']} vs requested {request.local_node_limit}"
            )

    now_str = _sqlite_timestamp(now)
    global_count = conn.execute(
        """
        select count(*) as cnt
        from executor_leases
        where executor_id=? and status='active' and expires_at>?
        """,
        (request.executor_id, now_str),
    ).fetchone()["cnt"]
    workspace_count = conn.execute(
        """
        select count(*) as cnt
        from executor_leases
        where workspace_id=? and executor_id=? and status='active' and expires_at>?
        """,
        (request.workspace_id, request.executor_id, now_str),
    ).fetchone()["cnt"]

    if global_count >= request.global_capacity:
        return None
    if workspace_count >= workspace_limit:
        return None

    if request.local_node_limit is not None:
        node_count = conn.execute(
            """
            select count(*) as cnt
            from executor_leases
            where workspace_id=? and workflow_key=? and node_key=? and status='active' and expires_at>?
            """,
            (request.workspace_id, request.workflow_key, request.node_key, now_str),
        ).fetchone()["cnt"]
        if node_count >= request.local_node_limit:
            return None

    cursor = conn.execute(
        """
        update job_nodes
        set status='running',
            stale_reason='',
            error_message='',
            started_at=?,
            finished_at=null
        where job_id=? and node_key=? and status in ('pending', 'ready', 'stale')
        """,
        (now_str, request.job_id, request.node_key),
    )
    if cursor.rowcount == 0:
        return None

    log_path = canonicalize_data_path(request.log_path, data_dir, "logs")
    cursor = conn.execute(
        """
        insert into node_runs(
            job_id, node_key, status, command_json, log_path, run_dir, session_dir, started_at
        )
        values (?, ?, 'running', ?, ?, '', '', ?)
        """,
        (request.job_id, request.node_key, json.dumps([]), log_path, now_str),
    )
    if cursor.lastrowid is None:
        raise sqlite3.OperationalError("node_runs insert did not produce a row id")
    node_run_id = cursor.lastrowid
    expires_at = now + timedelta(seconds=request.lease_ttl_seconds)
    conn.execute(
        """
        insert into executor_leases(
            id, execution_id, executor_id, workspace_id, job_id, workflow_key,
            node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        """,
        (
            lease_id,
            execution_id,
            request.executor_id,
            request.workspace_id,
            request.job_id,
            request.workflow_key,
            request.node_key,
            node_run_id,
            now_str,
            now_str,
            _sqlite_timestamp(expires_at),
        ),
    )

    conn.execute(
        "update jobs set status='running', updated_at=? where id=? and status != 'running'",
        (now_str, request.job_id),
    )

    return ClaimedExecution(
        lease_id=lease_id,
        execution_id=execution_id,
        node_run_id=node_run_id,
        executor_id=request.executor_id,
        workspace_id=request.workspace_id,
        job_id=request.job_id,
        workflow_key=request.workflow_key,
        node_key=request.node_key,
        capability=request.capability,
        log_path=request.log_path,
    )
