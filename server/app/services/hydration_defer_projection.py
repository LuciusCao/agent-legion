"""job 详情的 hydration defer 投影（#887；#1021 分片有效状态，从公告板拆出，文件预算）。"""

from __future__ import annotations

from typing import Any

from server.app.jobs import JobQueries
from server.app.services.hydration_defer_board import (
    HYDRATION_DEFER_BOARD,
    defer_scope,
    node_defer_view,
)
from server.app.workflows.definition import WorkflowDefinition
from server.app.workflows.sharding_batch import running_shard_nodes, shard_effective_statuses


def job_defer_views(
    job_db: JobQueries,
    job: dict[str, Any],
    definition: WorkflowDefinition,
    nodes: list[dict[str, Any]],
) -> dict[str, dict[str, list[str]] | None]:
    """节点 → ``hydration_defer`` 投影；无公告时不查库。

    #1021：节点状态与 worker hydration 同一分片有效状态口径——running 但仍有
    pending shard 的分片节点视为等待中（worker 公告正是按此把它列为受阻），
    否则剩余 shard 被挡住时提示会被 DB 原始 running 过滤掉。
    """
    job_id = str(job["id"])
    defers = HYDRATION_DEFER_BOARD.by_waiting_node(job_id)
    if not defers:
        return {}
    scope = defer_scope(definition, job)
    statuses = {str(node["node_key"]): str(node["status"]) for node in nodes}
    pending = job_db.pending_shard_nodes(job_id, running_shard_nodes(definition, statuses))
    statuses = shard_effective_statuses(statuses, pending)
    return {
        key: node_defer_view(notices, statuses.get(key, ""))
        for key, notices in defers.items()
        if key in scope
    }
