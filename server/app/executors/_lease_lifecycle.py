from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from server.app.db.connection import DatabaseConnection
from server.app.executors._file_promotion import promote_result_staged_moves
from server.app.executors._lease_control import (
    _pause_job_on_target_completion,
    lock_job_mutation_and_read_generation,
    sync_job_status,
)
from server.app.executors._lease_shards import finish_shard_execution
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors._lease_transient_retry import try_return_node_to_pending
from server.app.executors._path_canonicalization import canonicalize_finish_paths
from server.app.executors.models import ExecutionResult, FinishVerdict
from server.app.services import failure_classification
from server.app.services.job_run_dir_probe import finish_job_dir_candidates
from server.app.workflows.sharding import (
    failed_shard_error,
    on_shard_finished,
    shard_index_for_execution,
)

logger = logging.getLogger(__name__)


def heartbeat_lease(conn: DatabaseConnection, lease_id: str, ttl_seconds: int) -> bool:
    now = datetime.now(UTC)
    lease = conn.execute(
        "select status from executor_leases where id=%s",
        (lease_id,),
    ).fetchone()
    if lease is None or lease["status"] != "active":
        return False
    expires_at = now + timedelta(seconds=ttl_seconds)
    cursor = conn.execute(
        """
        update executor_leases
        set heartbeat_at=%s, expires_at=%s
        where id=%s and status='active'
        """,
        (database_timestamp(now), database_timestamp(expires_at), lease_id),
    )
    # Re-check the rowcount: a concurrent finish/expiry committed between the
    # SELECT above and this UPDATE leaves the lease untouched, and reporting
    # success would mask the loss of ownership.
    return cursor.rowcount > 0


def finish_lease(
    conn: DatabaseConnection, lease_id: str, result: ExecutionResult, data_dir: Path | None = None
) -> FinishVerdict:
    now = datetime.now(UTC)
    now_str = database_timestamp(now)
    lease = conn.execute("select * from executor_leases where id=%s", (lease_id,)).fetchone()
    if lease is None or lease["status"] != "active":
        return FinishVerdict(False)

    # EXEC-GENERATION-001：与 mutation 侧（lease_guarded_mutation）互斥后做
    # 代次 CAS。lease 落戳代次 != jobs 现值 = reset 后的迟到 finish：lease
    # 释放与 node_runs 历史行照常收尾，但跳过 job_nodes 翻转、
    # sync_job_status 与 until_node 暂停副作用，绝不盖掉新代次的重置行。
    generation_stale = lock_job_mutation_and_read_generation(conn, str(lease["job_id"])) != int(
        lease["execution_generation"]
    )
    if generation_stale:
        logger.info(
            "finish skipped node flip (stale generation): lease=%s job=%s node=%s gen=%s",
            lease_id,
            lease["job_id"],
            lease["node_key"],
            lease["execution_generation"],
        )
    elif result.staged_file_moves:
        # #759 review P1-1：Worker 结果归档的文件提升只在本代次闸内发生——
        # 解包先于闸落到 staging 目录，迟到（reset 后）的 finish 在此跳过，
        # 旧代次字节永远进不了新现场的 job_dir。提升失败整体回滚再上抛，
        # 不留半应用文件（与产物清单登记同一 FilePromotionGuard 纪律）。
        promote_result_staged_moves(result.staged_file_moves)

    conn.execute("update executor_leases set status='released' where id=%s", (lease_id,))

    node_run = conn.execute(
        "select n.log_path, j.workspace_id as job_workspace_id,"
        " j.storage_dir as job_storage_dir"
        " from node_runs n left join jobs j on j.id = n.job_id where n.id=%s",
        (lease["node_run_id"],),
    ).fetchone()
    effective_log_path, run_dir, session_dir = canonicalize_finish_paths(
        result,
        data_dir,
        node_run["log_path"] if node_run is not None else "",
        lease["node_key"],
        lease["job_id"],
        finish_job_dir_candidates(data_dir, node_run, str(lease["job_id"])),
    )
    failure_category, failure_detail = failure_classification.classify_execution_result(result)
    conn.execute(
        """
        update node_runs
        set status=%s, exit_code=%s, error_message=%s, failure_category=%s, failure_detail=%s,
            command_json=%s, log_path=%s, run_dir=%s, session_dir=%s,
            skill_version=%s, skill=%s, finished_at=%s, runner=%s
        where id=%s
        """,
        (
            result.status,
            result.exit_code,
            result.error_message,
            failure_category,
            failure_detail,
            json.dumps(list(result.command)),
            effective_log_path,
            run_dir,
            session_dir,
            result.skill_version,
            result.skill,
            now_str,
            result.runner or lease["executor_id"],
            lease["node_run_id"],
        ),
    )
    if finish_shard_execution(conn, lease, result, now_str, generation_stale=generation_stale):
        return FinishVerdict(True, generation_stale)

    if generation_stale:
        return FinishVerdict(True, True)

    if try_return_node_to_pending(conn, lease, result, failure_category, failure_detail):
        sync_job_status(conn, lease["job_id"])
        return FinishVerdict(True)

    conn.execute(
        """
        update job_nodes
        set status=%s, error_message=%s, finished_at=%s, failure_category=%s, failure_detail=%s
        where job_id=%s and node_key=%s
        """,
        (
            "completed" if result.status == "completed" else "failed",
            result.error_message,
            now_str,
            failure_category,
            failure_detail,
            lease["job_id"],
            lease["node_key"],
        ),
    )
    sync_job_status(conn, lease["job_id"])

    if result.status == "completed":
        _pause_job_on_target_completion(conn, lease["job_id"], lease["node_key"], now_str)

    return FinishVerdict(True)


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
