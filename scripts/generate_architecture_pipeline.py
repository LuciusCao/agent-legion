"""Video pipeline node extraction for scripts/generate_architecture.py."""

from typing import Any

from server.app.workflows.builtin import BUILTIN_WORKFLOW_DEFINITIONS


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
    """Extract the video pipeline node sequence from the built-in video_knowledge DAG."""
    nodes = BUILTIN_WORKFLOW_DEFINITIONS["video_knowledge"]["nodes"]

    ordered = _topological_node_order(nodes)
    lines = [
        f"**知识视频（{len(ordered)} 阶段）：**",
        " → ".join(f"`{key}`" for key in ordered),
    ]
    return "\n".join(lines) + "\n"
