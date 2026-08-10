from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import (
    TERMINAL_JOB_STATUSES,
    _execution_control_rejects_claim,
    _read_job_execution_control,
)
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors._path_canonicalization import canonicalize_data_path
from server.app.executors.models import ClaimedExecution, LeaseClaimRequest
from server.app.workflows.sharding import try_start_shard


def claim_lease(
    conn: DatabaseConnection, request: LeaseClaimRequest, data_dir: Path | None = None
) -> ClaimedExecution | None:
    """Attempt to claim a node run under capacity limits.

    Must run inside an active transaction. Returns None when capacity is
    exhausted or the node is not claimable; the caller is responsible for
    rolling back. Raises ValueError for configuration mismatches.
    """
    lease_id = str(uuid.uuid4())
    execution_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    # Capacity is scoped to an executor. Serializing only competing claims for
    # that executor makes the count-and-insert decision correct across API
    # replicas without blocking unrelated executors.
    conn.execute(
        "select pg_advisory_xact_lock(hashtext(%s))",
        (f"executor-claim:{request.executor_id}",),
    )

    current_control = _read_job_execution_control(conn, request.job_id)
    if _execution_control_rejects_claim(request, current_control):
        return None
    if current_control["status"] in TERMINAL_JOB_STATUSES:
        # 终态作业不得再启动节点：调度侧的 mark 缓存可能滞后（长事务以事务
        # 开始时刻的 updated_at 越过 watermark，见 mark_scan 模块文档），
        # 认领事务内必须以当前 jobs.status 为准。
        return None

    allocation = conn.execute(
        """
        select concurrency_limit
        from workspace_executor_allocations
        where workspace_id=%s and executor_id=%s
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
        where workspace_id=%s and workflow_key=%s and node_key=%s
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
            where workspace_id=%s and workflow_key=%s and node_key=%s
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

    now_str = database_timestamp(now)
    global_count_row = conn.execute(
        """
        select count(*) as cnt
        from executor_leases
        where executor_id=%s and status='active' and expires_at>%s
        """,
        (request.executor_id, now_str),
    ).fetchone()
    workspace_count_row = conn.execute(
        """
        select count(*) as cnt
        from executor_leases
        where workspace_id=%s and executor_id=%s and status='active' and expires_at>%s
        """,
        (request.workspace_id, request.executor_id, now_str),
    ).fetchone()
    global_count = int(global_count_row["cnt"]) if global_count_row is not None else 0
    workspace_count = int(workspace_count_row["cnt"]) if workspace_count_row is not None else 0

    if global_count >= request.global_capacity:
        return None
    if workspace_count >= workspace_limit:
        return None

    if request.local_node_limit is not None:
        node_count_row = conn.execute(
            """
            select count(*) as cnt
            from executor_leases
            where workspace_id=%s and workflow_key=%s and node_key=%s and status='active' and expires_at>%s
            """,
            (request.workspace_id, request.workflow_key, request.node_key, now_str),
        ).fetchone()
        node_count = int(node_count_row["cnt"]) if node_count_row is not None else 0
        if node_count >= request.local_node_limit:
            return None

    if request.shard_index is not None:
        started = try_start_shard(
            conn, request.job_id, request.node_key, request.shard_index, execution_id, now_str
        )
        if not started:
            return None
    else:
        cursor = conn.execute(
            """
            update job_nodes
            set status='running',
                stale_reason='',
                error_message='',
                started_at=%s,
                finished_at=null
            where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale')
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
        values (%s, %s, 'running', %s, %s, '', '', %s)
        returning id
        """,
        (request.job_id, request.node_key, json.dumps([]), log_path, now_str),
    )
    inserted = cursor.fetchone()
    if inserted is None:
        raise RuntimeError("node_runs insert did not return a row id")
    node_run_id = int(inserted["id"])
    expires_at = now + timedelta(seconds=request.lease_ttl_seconds)
    conn.execute(
        """
        insert into executor_leases(
            id, execution_id, executor_id, workspace_id, job_id, workflow_key,
            node_key, node_run_id, status, acquired_at, heartbeat_at, expires_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
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
            database_timestamp(expires_at),
        ),
    )

    conn.execute(
        "update jobs set status='running', updated_at=%s"
        " where id=%s and status not in ('running', 'completed', 'failed')",
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
        shard_index=request.shard_index,
    )
