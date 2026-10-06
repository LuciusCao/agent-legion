"""分片行只读查询（#1021）。

job 详情投影 hydration defer 时要与 worker 同一分片有效状态口径：running
分片节点是否仍有 pending shard。单独成文件以守 ``job_nodes`` 的预算。
"""

from __future__ import annotations

from collections.abc import Sequence

from server.app.jobs.queries.connection import ConnectionQueriesMixin
from server.app.workflows.sharding_batch import has_pending_shards_many


class JobShardReadQueriesMixin(ConnectionQueriesMixin):
    def pending_shard_nodes(self, job_id: str, node_keys: Sequence[str]) -> set[str]:
        """``node_keys`` 中仍有 pending shard 的节点。"""
        if not node_keys:
            return set()
        with self._connect_read() as conn:
            pairs = has_pending_shards_many(conn, [(job_id, key) for key in node_keys])
        return {node_key for _, node_key in pairs}
