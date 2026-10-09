from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from server.app.agent_broker.claim_node_limit import lock_code_pool
from server.app.db.connection import DatabaseConnection


def get_workspace_node_limits(conn: DatabaseConnection, workspace_id: str) -> list[dict[str, Any]]:
    """Per-node concurrency limits of one workspace (P-0.5: the only node knob)."""
    rows = conn.execute(
        "select node_key, concurrency_limit "
        "from workspace_node_limits where workspace_id=%s order by node_key",
        (workspace_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def replace_workspace_node_limits(
    conn: DatabaseConnection,
    workspace_id: str,
    node_limits: Sequence[Mapping[str, Any]],
) -> None:
    """Replace one workspace's node limits (delete + re-insert).

    #1149 对抗评审 P2-1：本 helper 是 workspace_node_limits 的唯一写入面，
    事务内先取共享 code-pool 锁再写任何 limit 行——配置写与持锁 claim（本地
    池 claim_lease / 远程节点级检查）串行化，持锁 claim 事务内看到的 limit
    现值因此稳定（insert/update/delete 都无法在其中途提交）；claim 侧
    probe「无行不取锁」与检查之间被插入提交的窗口由 claim 侧的
    node_limit_appeared skip 收口（见 claim_node_limit）。锁在本 helper 首句；
    唯一生产调用方（queries/workspace.update_workspace_configuration）的事务序
    为 workspaces 行 UPDATE → code-pool——全部 limit 写共享这一条固定内部序，
    且无其他 code-pool 持有者请求 workspaces 行，无环。
    """
    lock_code_pool(conn)
    conn.execute("delete from workspace_node_limits where workspace_id=%s", (workspace_id,))
    conn.executemany(
        "insert into workspace_node_limits "
        "(workspace_id, node_key, concurrency_limit) values (%s, %s, %s)",
        [(workspace_id, row["node_key"], row["concurrency_limit"]) for row in node_limits],
    )
