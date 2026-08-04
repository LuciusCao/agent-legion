from __future__ import annotations

from server.app.workflows.schema import WorkflowDefinitionError, WorkflowEdge, WorkflowNode


def _validate_acyclic(nodes: dict[str, WorkflowNode], edges: list[WorkflowEdge]) -> None:
    children: dict[str, list[str]] = {key: [] for key in nodes}
    for edge in edges:
        children[edge.source].append(edge.target)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_key: str) -> None:
        if node_key in visiting:
            raise WorkflowDefinitionError(f"Workflow contains a cycle at node {node_key}")
        if node_key in visited:
            return
        visiting.add(node_key)
        for child_key in children[node_key]:
            visit(child_key)
        visiting.remove(node_key)
        visited.add(node_key)

    for key in nodes:
        visit(key)
