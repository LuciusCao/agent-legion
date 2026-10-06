"""job-mutation 锁内的复查读（从 ``job_nodes`` 拆出，文件预算；行为不变）。

两个读都在调用方持有的事务连接上执行，用锁内当前状态替代锁外读数，
防 TOCTOU（run-to 重置集 #759、workflow 升级并发复查 codex #776）。
"""

from __future__ import annotations

from typing import Any


class JobLockedRereadQueriesMixin:
    @staticmethod
    def list_job_node_statuses_in_transaction(conn: Any, job_id: str) -> dict[str, str]:
        """锁内重读节点状态。run-to 重置集的 TOCTOU 纪律（#759）：重置、
        暂存与清单删除必须由同一份锁内当前状态驱动——锁外读数到取锁之间
        节点可能被 claim 并完成，用过期集合暂存会清掉已完成节点的权威
        产物。"""
        rows = conn.execute("select node_key, status from job_nodes where job_id=%s", (job_id,))
        return {str(row["node_key"]): str(row["status"]) for row in rows}

    @staticmethod
    def job_revision_identity_in_transaction(conn: Any, job_id: str) -> tuple[str, str] | None:
        """锁内重读 job 的 revision 钉 + 定义快照（upgrade 并发复查）。

        codex #776 复审 P1：并发升级在 job-mutation 锁上等待期间，前一个
        请求提交的 re-pin 会让锁外解析的 context 过期——升级应用前必须用
        锁内读数比对。返回 ``(workflow_revision_id, snapshot_json)``（空值
        归一为 ""，与 ``resolve_upgrade_context`` 的 already_current 判定
        同口径）；job 不存在返回 None。
        """
        row = conn.execute(
            "select workflow_revision_id, workflow_definition_snapshot_json from jobs where id=%s",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return (
            str(row["workflow_revision_id"] or ""),
            str(row["workflow_definition_snapshot_json"] or ""),
        )
