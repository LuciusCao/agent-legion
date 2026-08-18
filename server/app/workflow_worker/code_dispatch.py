"""Node code resolution for the code-pool dispatch path.

Extracted from ``schedule.py`` to keep it within its size budget. Every
executor-routed node is code-routed (P-0.5): the code text resolves frozen
job pin → workspace published → global factory seed (EXEC-CODE-002; #96
retired the repo-file path binding), and a node without any published code
fails fast as a configuration error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.app.services.node_code_resolution import resolve_dispatch_node_code

if TYPE_CHECKING:
    from server.app.workflow_worker.thread import WorkflowWorkerThread
    from server.app.workflows.definition import WorkflowNode


def resolve_code_node_dispatch(
    worker: WorkflowWorkerThread,
    workspace_id: str,
    workflow_key: str,
    node: WorkflowNode,
    batch_payload: dict[str, Any] | None,
) -> str:
    """Return the node code text, or raise ValueError when unrunnable.

    A frozen job pin fails closed: a hash mismatch raises, and a pinned
    version missing at BOTH scopes is data corruption and raises too — never
    silently substituted with the current published code (EXEC-CODE-003).
    """
    frozen_pins = (batch_payload or {}).get("node_code_versions") or {}
    node_code = resolve_dispatch_node_code(
        worker.job_db.path,
        worker.settings.executor_runtime.workflows.custom_nodes_enabled,
        workspace_id,
        workflow_key,
        node.key,
        frozen_pins.get(node.key),
    )
    if node_code is None:
        raise ValueError(
            f"capability {node.capability!r} has no published node code "
            "(workspace version or global factory seed, EXEC-CODE-002)"
        )
    return node_code
