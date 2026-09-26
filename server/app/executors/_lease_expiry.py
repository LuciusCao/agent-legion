"""lease 过期清扫面（自 ``_lease_lifecycle`` 拆出的文件预算姊妹模块）。

``expire_stale_leases`` 批扫到期 lease 并逐行收尾（EXEC-GENERATION-001：
每行在 job-mutation advisory 锁内做代次 CAS，批序共用全库唯一序
(ws 锁键, job_id)）；Agent Worker lease（'agent:%'）归 Agent broker
sweep（requeue-with-retry 语义），本面刻意排除。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import (
    lock_job_mutation_and_read_generation,
    sync_job_status,
)
from server.app.executors._lease_transactions import database_timestamp
from server.app.workflows.sharding import (
    failed_shard_error,
    on_shard_finished,
    shard_index_for_execution,
)

logger = logging.getLogger(__name__)


def expire_stale_leases(conn: DatabaseConnection, now: datetime) -> list[str]:
    now_str = database_timestamp(now)
    # Agent Worker leases ('agent:%') are owned by the Agent broker sweep
    # (requeue-with-retry semantics); expiring them here would fail the node
    # and job while the broker later requeues the request, leaving the job
    # failed with a permanently queued request.
    rows = conn.execute(
        """
        select l.id, l.job_id, l.node_key, l.node_run_id, l.execution_id,
               l.execution_generation, j.workspace_id,
               hashtext('agent-ws:' || j.workspace_id)::int as ws_lock_key
        from executor_leases l
        join jobs j on j.id = l.job_id
        where l.status='active' and l.expires_at<=%s
          and not starts_with(l.executor_id, 'agent:')
        """,
        (now_str,),
    ).fetchall()
    expired: list[str] = []
    # EXEC-GENERATION-001：每行会取 job-mutation advisory 锁（xact 级、不随
    # 语句/SAVEPOINT 释放），全库批路径共用唯一序 (ws 锁键, job_id)——与
    # agent claim 批的 agent 块（claim_batch_tx._lock_order_sorted，同一
    # hashtext('agent-ws:' || workspace_id) 键）及 finish_many/recover/
    # sweep 相同，防批间 AB-BA。
    for row in sorted(rows, key=lambda r: (int(r["ws_lock_key"]), str(r["job_id"]))):
        if _expire_lease_row(conn, row, now_str):
            expired.append(row["id"])
    return expired


def _expire_lease_row(conn: DatabaseConnection, row: dict[str, Any], now_str: str) -> bool:
    """Expire one stale lease row; False when it left the stale set concurrently.

    The guard predicates are re-evaluated by PostgreSQL against the newest
    committed row version when a concurrent finish/heartbeat touched the row
    after this transaction's SELECT, so a lease that was released or renewed
    in between is left untouched instead of being clobbered to 'expired'.

    EXEC-GENERATION-001: the lease/run side (lease → expired, node_run →
    failed) always settles; the job_nodes/jobs flip runs only when the lease's
    epoch still matches jobs.execution_generation — a late expiry from an old
    epoch must not fail nodes a reset just re-queued.
    """
    generation_stale = lock_job_mutation_and_read_generation(conn, str(row["job_id"])) != int(
        row["execution_generation"]
    )
    cursor = conn.execute(
        """
        update executor_leases set status='expired'
        where id=%s and status='active' and expires_at<=%s
        """,
        (row["id"], now_str),
    )
    if cursor.rowcount == 0:
        return False
    conn.execute(
        """
        update node_runs
        set status='failed', error_message='lease expired', finished_at=%s
        where id=%s
        """,
        (now_str, row["node_run_id"]),
    )
    if generation_stale:
        logger.info(
            "lease expiry skipped node flip (stale generation): lease=%s job=%s node=%s gen=%s",
            row["id"],
            row["job_id"],
            row["node_key"],
            row["execution_generation"],
        )
    shard_index = shard_index_for_execution(
        conn, str(row["job_id"]), str(row["node_key"]), str(row["execution_id"])
    )
    if shard_index is not None:
        # node_shards 行是执行记录（与 node_runs 同侧），照常收尾；只有
        # job_nodes 聚合翻转与 sync 走代次闸门。
        aggregate = on_shard_finished(
            conn,
            str(row["job_id"]),
            str(row["node_key"]),
            shard_index,
            "failed",
            error_message="lease expired",
        )
        if aggregate in ("completed", "failed") and not generation_stale:
            error_message = failed_shard_error(conn, str(row["job_id"]), str(row["node_key"]))
            # Status guard mirrors finish_shard_execution: a late expiry
            # racing a reset/rerun must not overwrite a terminal node.
            conn.execute(
                """
                update job_nodes
                set status=%s, stale_reason='', error_message=%s, finished_at=%s
                where job_id=%s and node_key=%s
                    and status in ('pending', 'ready', 'stale', 'running')
                """,
                (aggregate, error_message, now_str, row["job_id"], row["node_key"]),
            )
            sync_job_status(conn, str(row["job_id"]))
        return True
    if generation_stale:
        return True
    conn.execute(
        """
        update job_nodes
        set status='failed', stale_reason='', error_message='lease expired', finished_at=%s
        where job_id=%s and node_key=%s
        """,
        (now_str, row["job_id"], row["node_key"]),
    )
    sync_job_status(conn, row["job_id"])
    conn.execute(
        """
        update jobs
        set status='failed', updated_at=%s
        where id=%s and status != 'failed'
        """,
        (now_str, row["job_id"]),
    )
    return True
