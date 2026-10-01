"""upgrade-workflow 结果 dict 的统一构造（issue #645）。

skipped/failed/succeeded 三族结果形状统一携带 mode + 继承统计
（``kept_node_count`` / ``rerun_node_count``，字段名带 _node_count 后缀
避免与 rerun-by-failure 的 rerun_nodes 节点列表语义相撞），前端与测试
断言依赖该稳定形状。
"""

from __future__ import annotations

from typing import Any


def upgrade_result(
    job_id: str,
    status: str,
    reason_code: str | None = None,
    message: str | None = None,
    *,
    mode: str = "clean",
    kept: int = 0,
    rerun: int = 0,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "operation": "upgrade_workflow",
        "status": status,
        "node_key": None,
        "reason_code": reason_code,
        "message": message,
        "mode": mode,
        "kept_node_count": kept,
        "rerun_node_count": rerun,
    }
