from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.workflows.definition import WorkflowDefinition, WorkflowNode

RUNNABLE_STATUSES = {"pending", "ready", "stale"}


def _inputs_exist(node: WorkflowNode, artifact_dir: Path) -> bool:
    return all((artifact_dir / name).exists() for name in node.inputs)


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
    for node in definition.nodes.values():
        if node_statuses.get(node.key, "pending") not in RUNNABLE_STATUSES:
            continue
        if any(node_statuses.get(dep) != "completed" for dep in node.after):
            continue
        if not _inputs_exist(node, artifact_dir):
            continue
        ready.append(node)
    return ready


def downstream_nodes(definition: WorkflowDefinition, node_key: str) -> list[str]:
    children: dict[str, list[str]] = {key: [] for key in definition.nodes}
    for candidate in definition.nodes.values():
        for dep in candidate.after:
            children[dep].append(candidate.key)

    seen: set[str] = set()
    ordered: list[str] = []

    def visit(key: str) -> None:
        for child in children.get(key, []):
            if child in seen:
                continue
            seen.add(child)
            ordered.append(child)
            visit(child)

    visit(node_key)
    return ordered


def summarize_job_status(statuses: list[str]) -> str:
    if not statuses:
        return "queued"
    if any(status == "running" for status in statuses):
        return "running"
    if any(status == "failed" for status in statuses):
        return "failed"
    if all(status == "completed" for status in statuses):
        return "completed"
    return "queued"
