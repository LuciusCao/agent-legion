from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.workflows.conditions import selected_edges
from server.app.workflows.definition import WorkflowDefinition, WorkflowNode
from server.app.workflows.workflow_branching import (
    RUNNABLE_STATUSES,
    _incoming_edges,
    effective_node_statuses,
)
from server.app.workflows.workflow_consumption import artifact_producers

TERMINAL_SUCCESS_STATUSES = {"completed", "not_applicable"}


def _inputs_exist(node: WorkflowNode, artifact_dir: Path) -> bool:
    return all((artifact_dir / name).exists() for name in node.inputs)


def _has_unfinished_implicit_producer(
    node: WorkflowNode,
    node_statuses: dict[str, str],
    producers: dict[str, set[str]],
) -> bool:
    """隐式消费边的完成屏障（#759）：任一 input 的非自身生产者未达终态
    （completed/not_applicable）时不可运行。

    调度器此前只以输入文件存在为凭——RMW 产物在重置后被刻意保留（#114，
    删除会让 RMW 节点死等自己产生的输入），文件仍在不代表生产者已重写，
    消费者会与生产者并发重跑读到旧值。not_applicable 生产者不设障（其
    产物本轮不刷新，读既有文件与文件存在语义一致）；隐式边成环则互堵
    （fail-closed——环本来就没有正确顺序，停住比静默跑错结果可观测）。
    """
    for name in node.inputs:
        for producer in producers.get(name, ()):
            if producer == node.key:
                continue
            if node_statuses.get(producer, "pending") not in TERMINAL_SUCCESS_STATUSES:
                return True
    return False


def _node_statuses(job_db: Any, job_id: str) -> dict[str, str]:
    return {node["node_key"]: node["status"] for node in job_db.list_job_nodes(job_id)}


def _refresh_job_status(job_db: Any, job_id: str) -> None:
    nodes = job_db.list_job_nodes(job_id)
    status = summarize_job_status([node["status"] for node in nodes])
    error_message = ""
    if status == "failed":
        error_message = next(
            (str(node["error_message"]) for node in nodes if node.get("error_message")),
            "",
        )
    job_db.update_job_status(job_id, status, error_message)


def find_ready_nodes(
    definition: WorkflowDefinition,
    node_statuses: dict[str, str],
    artifact_dir: Path,
) -> list[WorkflowNode]:
    ready: list[WorkflowNode] = []
    incoming = _incoming_edges(definition)
    producers = artifact_producers(definition)
    # Start nodes are definitionally completed: never runnable themselves,
    # and their outgoing edges are always satisfied (EXEC-WORKFLOW-START-001).
    node_statuses = effective_node_statuses(definition, node_statuses)
    for node in definition.nodes.values():
        if node_statuses.get(node.key, "pending") not in RUNNABLE_STATUSES:
            continue
        active_incoming = selected_edges(incoming[node.key], artifact_dir)
        if incoming[node.key] and not active_incoming:
            continue
        if any(node_statuses.get(edge.source) == "not_applicable" for edge in active_incoming):
            continue
        if any(node_statuses.get(edge.source) != "completed" for edge in active_incoming):
            continue
        if not _inputs_exist(node, artifact_dir):
            continue
        if _has_unfinished_implicit_producer(node, node_statuses, producers):
            continue
        ready.append(node)
    return ready


def summarize_job_status(statuses: list[str]) -> str:
    if not statuses:
        return "queued"
    if any(status == "running" for status in statuses):
        return "running"
    if any(status == "failed" for status in statuses):
        return "failed"
    # A parked approval gate outranks queued once nothing is running: the
    # job is waiting on a human, not on capacity (EXEC-APPROVAL-001).
    if any(status == "awaiting_approval" for status in statuses):
        return "awaiting_approval"
    if all(status in TERMINAL_SUCCESS_STATUSES for status in statuses):
        return "completed"
    return "queued"
