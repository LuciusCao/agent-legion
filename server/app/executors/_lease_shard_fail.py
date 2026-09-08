"""No-lease shard failure for dispatch-time resolution errors (#520 P2).

The node-level configuration-failure write (``_lease_config_failure``)
fails the ``job_nodes`` row through a pending/ready/stale status guard —
correct for ordinary nodes, but a shard node that an earlier fan-out round
already flipped to ``running`` is invisible to that guard: a mid-fan-out
resolve failure (published code archived between rounds, config drift)
would record nothing anywhere, leaving the shard pending and the node and
job running forever. This module is the shard-granularity sibling: the
shard row goes terminal through ``on_shard_finished`` and the aggregate
verdict decides the node, exactly like a shard execution failure
(``_lease_shards.finish_shard_execution``). PR #520 review round 2 adds
the dispatch-identity re-guard (P1), the synthetic failed node_run
(P2-1), and the repo-routed transaction + post-commit broadcast (P2-2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from server.app.db.connection import DatabaseConnection
from server.app.db.retry import retry_on_database_conflict
from server.app.db.transaction import write_transaction
from server.app.executors._lease_control import sync_job_status
from server.app.executors._lease_transactions import database_timestamp
from server.app.executors._path_canonicalization import canonicalize_data_path
from server.app.services import failure_classification
from server.app.workflows.sharding import failed_shard_error, on_shard_finished


class _ShardFailRepo(Protocol):
    """Repository seam for the connect-and-transact unit (leases.py delegate)."""

    # 仓库的 data_dir 是必填 Path（构造签名），事务体接受 None（历史调用
    # 方直接传 conn 时无 data_dir），Protocol 按宽的一侧声明。
    path: str
    data_dir: Path


def read_shard_dispatch_generation(conn: DatabaseConnection, job_id: str, node_key: str) -> str:
    """Read the node's dispatch generation (``job_nodes.created_at``).

    Every shard-table rebuild refreshes this column in the SAME transaction
    that recreates the shard rows (rerun: ``mark_nodes_for_rerun`` /
    ``apply_run_to``; orphan recovery: ``_recover_orphaned_job``), while
    same-round requeue resets (``reset_shard_for_requeue``) leave it alone —
    so the value separates "this shard row belongs to the round the caller
    dispatched" from "a rerun rebuilt it". Dispatch lanes snapshot it when
    they hand the shard to a lane; the failure write re-guards on it.
    """
    row = conn.execute(
        "select created_at from job_nodes where job_id=%s and node_key=%s",
        (job_id, node_key),
    ).fetchone()
    return str(row["created_at"]) if row is not None else ""


def fail_shard_repo(
    repo: _ShardFailRepo,
    job_id: str,
    node_key: str,
    shard_index: int,
    error_message: str,
    *,
    dispatch_generation: str = "",
    log_path: str = "",
) -> bool:
    """Connect-transact-retry unit behind ``ExecutorLeaseRepository.fail_shard``.

    同 ``_lease_write_paths`` 的写路径结构：事务体独立、整单元按数据库冲突
    重试；广播由仓库方法在提交成功后执行（P2-2，见 leases.py）。
    """
    return retry_on_database_conflict(
        lambda: _fail_shard_once(
            repo, job_id, node_key, shard_index, error_message, dispatch_generation, log_path
        )
    )


def _fail_shard_once(
    repo: _ShardFailRepo,
    job_id: str,
    node_key: str,
    shard_index: int,
    error_message: str,
    dispatch_generation: str,
    log_path: str,
) -> bool:
    with write_transaction(repo.path) as conn:
        return fail_shard_without_lease(
            conn,
            job_id,
            node_key,
            shard_index,
            error_message,
            dispatch_generation=dispatch_generation,
            log_path=log_path,
            data_dir=repo.data_dir,
        )


def fail_shard_without_lease(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    shard_index: int,
    error_message: str,
    *,
    dispatch_generation: str = "",
    log_path: str = "",
    data_dir: Path | None = None,
) -> bool:
    """Fail one shard for a dispatch-time resolution error, without a lease.

    PR #520 review P2：终结具体 shard 而非整个节点——多轮 fan-out（容量或
    max_concurrency 拆轮）中节点可能已被先前 shard 置 running，节点级写
    的 status guard 对它是 no-op。``on_shard_finished`` 记 shard 终态并算
    聚合（any-failed 优先级使聚合立即变 failed）；节点推进镜像
    ``finish_shard_execution``：guard 含 running，error 取第一个失败
    shard，随后 ``sync_job_status`` 聚合 job。该 shard 从未被 claim，
    ``on_shard_finished`` 的无 guard 写因此安全；与本 pass 并发 claim 同
    一 shard 的极窄竞态由后到者收敛（claim 的 pending-only guard 挡住先
    失败的行，finish 覆盖先失败的行）。

    PR #520 review P1（迟到失败的身份校验）：远程 fan-out 的异步
    ``_enqueue`` 失败可能晚于用户 rerun 到达——rerun 删除并按相同
    (job_id, node_key, shard_index) 重建 shard 行，无条件的
    ``on_shard_finished`` 会把新一轮的 pending shard 错杀成 failed。
    沿用 ``lease_guarded_mutation`` 的「写事务内 re-guard」模式：调用方
    在 dispatch 时刻快照节点代次（``created_at``，rerun 重建 shard 行的
    同一事务会刷新它——见 ``read_shard_dispatch_generation``），本写路径
    在落终态前校验「shard 行仍是本轮 dispatch 看到的 pending 未绑定状态
    且 节点代次未变」，不匹配即丢弃（返回 False）——旧轮的迟到失败自然
    消解，新轮的 pending shard 不被污染。空快照（同步失败路径，dispatch
    与失败之间无事务可插入）在事务内现读代次，语义相同。
    """
    if not _shard_still_in_dispatch_round(conn, job_id, node_key, shard_index, dispatch_generation):
        return False
    failure_category, failure_detail = failure_classification.resolve_failure_fields(
        "failed", None, error_message
    )
    now_str = database_timestamp(datetime.now(UTC))
    aggregate = on_shard_finished(
        conn, job_id, node_key, shard_index, "failed", error_message=error_message
    )
    # P2-1：镜像 _failed_node_recording 的 synthetic node_run——dispatch 失败
    # 的 shard 从未被 claim，没有执行侧 node_run；不写则该失败对只查
    # node_runs 的 list_failed_node_runs（按类别诊断 / 批量 rerun）不可见。
    conn.execute(
        """
        insert into node_runs(
            job_id, node_key, status, command_json, log_path,
            run_dir, session_dir, started_at, finished_at, error_message,
            failure_category, failure_detail
        )
        values (%s, %s, 'failed', '[]', %s, '', '', %s, %s, %s, %s, %s)
        """,
        (
            job_id,
            node_key,
            canonicalize_data_path(log_path, data_dir, "logs"),
            now_str,
            now_str,
            error_message,
            failure_category,
            failure_detail,
        ),
    )
    if aggregate == "failed":
        # 节点行同步归类（与节点级 fail_without_lease 一致）；shard 执行失败
        # 路径不设这两个字段，此处取更严的一侧——dispatch 失败的根因就是
        # 分类规则的输入。
        conn.execute(
            """
            update job_nodes
            set status='failed', error_message=%s, finished_at=%s,
                failure_category=%s, failure_detail=%s
            where job_id=%s and node_key=%s
                and status in ('pending', 'ready', 'stale', 'running')
            """,
            (
                failed_shard_error(conn, job_id, node_key),
                now_str,
                failure_category,
                failure_detail,
                job_id,
                node_key,
            ),
        )
        sync_job_status(conn, job_id)
    return True


def _shard_still_in_dispatch_round(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    shard_index: int,
    dispatch_generation: str,
) -> bool:
    """Re-guard the dispatch identity before any terminal write (P1).

    两道校验，全在写事务内、在 ``on_shard_finished`` 更新 shard 行之前：
    ① shard 行仍是 pending 且未绑定 execution——被 claim 过（本轮或重建
    轮）的行不是 dispatch 失败的对象，rerun 删除后未重建的行直接不存在；
    ② 节点代次等于 dispatch 时刻的快照——rerun/orphan-recovery 重建 shard
    行必刷新 ``job_nodes.created_at``，不匹配说明行是新一轮的，旧轮失败就
    此丢弃。空快照在事务内现读代次（同步失败路径：dispatch 与失败之间
    是同一 poll pass 的几行代码，rerun 的 lease_guarded_mutation 被 running
    节点挡住，读到的即 dispatch 时的代次）。
    """
    row = conn.execute(
        "select status, execution_id from node_shards"
        " where job_id=%s and node_key=%s and shard_index=%s",
        (job_id, node_key, shard_index),
    ).fetchone()
    if row is None or row["status"] != "pending" or row["execution_id"]:
        return False
    generation = dispatch_generation or read_shard_dispatch_generation(conn, job_id, node_key)
    if not generation:
        # 无节点行：shard 行无归属（fan-out 不会发生），无处落失败。
        return False
    node = conn.execute(
        "select created_at from job_nodes where job_id=%s and node_key=%s",
        (job_id, node_key),
    ).fetchone()
    return node is not None and str(node["created_at"]) == generation


__all__ = [
    "fail_shard_repo",
    "fail_shard_without_lease",
    "read_shard_dispatch_generation",
]
