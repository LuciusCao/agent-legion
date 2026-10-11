"""Schema v94: ``agent_workers.node_concurrency_limits_json`` — Worker 节点级并发上限（#1158）。

两层并发模型的 Worker 层：workspace 全局上限（workspace_node_limits，#1149）
保护共享资源，本列承载每台机器 × node_key 的机器资源上限。Worker 在每次
claim 请求里重声明该映射（与 max_concurrency 同款的 sync_declared_capacity
热同步，改配置下一次 claim 生效、无需重注册）；Host claim 判定按
(worker_id, node_key) 计数 agent_execution_requests 的 claimed 行比对上限。

Same guarded-ALTER home rule as v87/v89/v90: the column lives ONLY here
(postgres_schema.sql sits at its budget ceiling), idempotent on replay, and
both install paths run the chain anyway. Default '{}' = 无限制（未声明与空
map 同义，零回归面）。
"""

from __future__ import annotations

from typing import Any

_NODE_LIMITS_DDL = """
alter table agent_workers
  add column if not exists node_concurrency_limits_json text not null default '{}';
"""


def migrate_agent_worker_node_limits(conn: Any) -> None:
    """Add the per-node concurrency limits column (v94, #1158)."""
    conn.execute(_NODE_LIMITS_DDL)
