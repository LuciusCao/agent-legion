"""Code-capability node code resolution for the dispatch path.

Extracted from ``schedule.py`` to keep it within its size budget. Resolves
the node code text for code-kind executors (frozen job pin → workspace
published → global factory seed, EXEC-CODE-002; #96 retired the repo-file
path binding) and fails fast when a capability has no code to run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.executors.config import CodeExecutorConfig
from server.app.services.node_code_resolution import (
    require_runnable_capability,
    resolve_dispatch_node_code,
)

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread
    from server.app.workflows.definition import WorkflowNode


def resolve_code_node_dispatch(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node: WorkflowNode,
    executor_id: str,
    batch_payload: dict[str, Any] | None,
) -> str | None:
    """Return the node code text for a code-executor node, or None for
    non-code executors.

    Non-code executors short-circuit to None without a DB read. Frozen job
    version wins over the workspace published version, then the global
    factory seed; a frozen-pin hash mismatch raises ValueError (fail closed,
    EXEC-CODE-003), and a capability without any published code raises
    ValueError (EXEC-CODE-002) — the caller fails the node as a config error.
    """
    definition = worker.registry.definitions().get(executor_id)
    if not isinstance(definition, CodeExecutorConfig):
        return None
    frozen_pins = (batch_payload or {}).get("node_code_versions") or {}
    node_code = resolve_dispatch_node_code(
        worker.job_db.path,
        worker.settings.executor_runtime.workflows.custom_nodes_enabled,
        workspace_id,
        workflow_key,
        node.key,
        frozen_pins.get(node.key),
    )
    require_runnable_capability(definition.capabilities, node.capability, node_code)
    return node_code
