"""Demo workflow node extraction for scripts/generate_architecture.py."""

from typing import Any

from server.app.workflows.builtin import BUILTIN_WORKFLOW_DEFINITIONS
from server.app.workflows.builtin_demo import DEMO_WORKFLOW_KEY


def _topological_node_order(nodes: dict[str, Any]) -> list[str]:
    """Return node keys in dependency order based on their `after` declarations."""
    remaining = dict(nodes)
    ordered: list[str] = []
    while remaining:
        progressed = False
        for key, spec in list(remaining.items()):
            after = spec.get("after") if isinstance(spec, dict) else None
            deps = [dep for dep in (after or []) if dep in nodes]
            if all(dep in ordered for dep in deps):
                ordered.append(key)
                del remaining[key]
                progressed = True
        if not progressed:
            # Cycle or dangling dependency: fall back to definition order.
            ordered.extend(remaining)
            break
    return ordered


def extract_pipeline_phases() -> str:
    """Extract the node sequence from the built-in demo workflow DAG."""
    nodes = BUILTIN_WORKFLOW_DEFINITIONS[DEMO_WORKFLOW_KEY]["nodes"]

    ordered = _topological_node_order(nodes)
    lines = [
        f"**{DEMO_WORKFLOW_KEY}（{len(ordered)} 节点）：**",
        " → ".join(f"`{key}`" for key in ordered),
    ]
    return "\n".join(lines) + "\n"
