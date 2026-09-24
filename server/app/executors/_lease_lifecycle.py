from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import (
    _pause_job_on_target_completion,
    lock_job_mutation_and_read_generation,
    sync_job_status,
)
from server.app.executors._lease_finish_promotion import promote_result_staged_moves_contained
from server.app.executors._lease_shards import finish_shard_execution
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors._lease_transient_retry import try_return_node_to_pending
from server.app.executors._path_canonicalization import canonicalize_finish_paths
from server.app.executors.models import ExecutionResult, FinishVerdict
from server.app.services import failure_classification
from server.app.services.job_run_dir_probe import finish_job_dir_candidates

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
    current_generation = lock_job_mutation_and_read_generation(conn, str(lease["job_id"]))
    # codex #774 P1：锁前的 lease 读取可能已过桥——worker-loss sweep 持锁
    # 删 lease 重排队（不 bump 代次）、或并发 finish 先释放同 lease 时，
    # 上面的 SELECT 仍看到 active。锁内重读（agent_broker.sweepers 同一纪
    # 律）：lease 不再 active 的 finish 什么也不做——不提升文件、不翻转
    # node_run/job_nodes，过期 Worker 的归档覆盖不了重排队执行的新现场；
    # 调用方拿到 FinishVerdict(False) 即 409，与锁前 miss 同语义。
    lease = conn.execute("select * from executor_leases where id=%s", (lease_id,)).fetchone()
    if lease is None or lease["status"] != "active":
        return FinishVerdict(False)
    generation_stale = current_generation != int(lease["execution_generation"])
    if generation_stale:
        logger.info(
            "finish skipped node flip (stale generation): lease=%s job=%s node=%s gen=%s",
            lease_id,
            lease["job_id"],
            lease["node_key"],
            lease["execution_generation"],
        )
        if result.staged_file_moves:
            # codex #774 P2：staged moves 随闸全部跳过意味着 view 探出的
            # run_dir（归档成员的落点）从未落盘、临时视图随后被清理——置
            # 空让 canonicalize 回退到文件系统派生（只记录真实存在的路
            # 径），旧 node_run 不再持久化一个从未落盘且可能被复用的位置。
            result = replace(result, run_dir="")
    elif result.staged_file_moves:
        # #759 review P1-1：Worker 结果归档的文件提升只在本代次闸内发生——
        # 解包先于闸落到 staging 目录，迟到（reset 后）的 finish 在此跳过，
        # 旧代次字节永远进不了新现场的 job_dir。提升失败整体回滚、
        # completed 转 failed 照常提交（_lease_finish_promotion 的兜底臂，
        # #759 对抗复审 P2 族），不留半应用文件也不毒化 lease。
        result = promote_result_staged_moves_contained(result, lease_id=lease_id)

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
