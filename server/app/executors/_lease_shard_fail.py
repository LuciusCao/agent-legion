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
(``_lease_shards.finish_shard_execution``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from server.app.db.connection import DatabaseConnection
from server.app.executors._lease_control import sync_job_status
from server.app.executors._lease_transactions import database_timestamp
from server.app.workflows.sharding import failed_shard_error, on_shard_finished


def fail_shard_without_lease(
    conn: DatabaseConnection,
    job_id: str,
    node_key: str,
    shard_index: int,
    error_message: str,
) -> None:
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
    """
    aggregate = on_shard_finished(
        conn, job_id, node_key, shard_index, "failed", error_message=error_message
    )
    if aggregate == "failed":
        conn.execute(
            """
            update job_nodes
            set status='failed', error_message=%s, finished_at=%s
            where job_id=%s and node_key=%s and status in ('pending', 'ready', 'stale', 'running')
            """,
            (
                failed_shard_error(conn, job_id, node_key),
                database_timestamp(datetime.now(UTC)),
                job_id,
                node_key,
            ),
        )
        sync_job_status(conn, job_id)


__all__ = ["fail_shard_without_lease"]
