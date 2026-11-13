"""Publish-side use of the Agent result-archive reserved-name guard."""

from __future__ import annotations

from typing import Any

from server.app.agent_broker.manifest_guard import require_unreserved_output_names


def require_unreserved_workflow_outputs(definition: Any) -> None:
    """Reject before any workflow revision write; keep enqueue fail-closed too.

    Only dispatchable code/Agent nodes produce result archives; start and
    approval declarations never enter an execution manifest. The first error
    names its node and uses the exact enqueue-side collision diagnostic.
    """
    for key, node in definition.executable_nodes.items():
        if node.node_type not in {"code", "agent"}:
            continue
        try:
            require_unreserved_output_names({"expected_outputs": node.outputs})
        except ValueError as exc:
            raise ValueError(f"node {key}: {exc}") from exc
