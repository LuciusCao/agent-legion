"""Failed-upstream guard for rerun / run-to start-node selection.

Rerun / run-to-with-start only reset the start node and its downstream; a
failed ancestor stays failed and the scheduler requires completed upstreams,
so the start node could never become ready — the job would sit queued
forever with a failed node. Shared by ``check_rerun_eligibility``,
``rerun_ineligible_from_nodes`` and run-to-with-start so every entry point
rejects such selections with the same error.
"""

from __future__ import annotations

from typing import Any

from server.app.services.job_operation_error import JobOperationError
from server.app.workflows.workflow_consumption import dependency_ancestors


def failed_upstream_node_keys(
    definition: Any, nodes: list[dict[str, Any]], node_key: str
) -> list[str]:
    """Failed ancestors of ``node_key`` that a rerun would leave behind.

    合并上游（显式边 ∪ 隐式生产边，#759）：隐式生产者 failed 也必须
    拦住——调度就绪对隐式生产者有完成屏障，放行必然卡死。
    """
    statuses = {str(node["node_key"]): str(node["status"]) for node in nodes}
    return [
        key for key in dependency_ancestors(definition, node_key) if statuses.get(key) == "failed"
    ]


def upstream_failed_error(
    job_id: str, node_key: str, failed_keys: list[str], *, operation: str = "rerun"
) -> JobOperationError:
    names = ", ".join(failed_keys)
    detail = f"Upstream node(s) failed: {names}; rerun from the failed node instead"
    return JobOperationError(job_id, operation, "skipped", node_key, "upstream_failed", detail)


def raise_if_failed_upstream(
    definition: Any,
    nodes: list[dict[str, Any]],
    start_node_key: str,
    job_id: str,
    operation: str,
    error_node_key: str,
) -> None:
    """Raise-variant for raise-style services; ``error_node_key`` is the
    operation's own key (run-to reports the target, not the start)."""
    failed = failed_upstream_node_keys(definition, nodes, start_node_key)
    if failed:
        raise upstream_failed_error(job_id, error_node_key, failed, operation=operation)


def raise_if_failed_upstream_in_tx(
    job_db: Any,
    conn: Any,
    definition: Any,
    start_node_key: str,
    job_id: str,
    operation: str,
    error_node_key: str,
) -> None:
    """mutation 锁内的 failed-upstream 重查（#759 invariant 5 / TOCTOU）：
    锁外预检到取锁之间上游可能转 failed（无 lease 的 config-failure 路径
    拦不住），状态相关的资格判定必须在锁内用当前状态重算。"""
    statuses = job_db.list_job_node_statuses_in_transaction(conn, job_id)
    nodes = [{"node_key": key, "status": status} for key, status in statuses.items()]
    raise_if_failed_upstream(definition, nodes, start_node_key, job_id, operation, error_node_key)
