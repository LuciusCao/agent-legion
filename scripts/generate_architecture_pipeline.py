"""Video pipeline node extraction for scripts/generate_architecture.py."""

from pathlib import Path
from typing import Any

import yaml


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


def extract_pipeline_phases(root: Path) -> str:
    """Extract video pipeline node sequence from config/workflows/video_knowledge.yaml."""
    workflow_file = root / "config" / "workflows" / "video_knowledge.yaml"
    if not workflow_file.exists():
        return "_No video_knowledge.yaml found._\n"

    try:
        data = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return "_Could not parse video_knowledge.yaml._\n"

    nodes = data.get("nodes") if isinstance(data, dict) else None
    if not isinstance(nodes, dict) or not nodes:
        return "_No pipeline nodes found._\n"

    ordered = _topological_node_order(nodes)
    lines = [
        f"**知识视频（{len(ordered)} 阶段）：**",
        " → ".join(f"`{key}`" for key in ordered),
    ]
    return "\n".join(lines) + "\n"
