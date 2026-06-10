from __future__ import annotations

from pathlib import Path

from server.app.pipelines.definition import PipelineDefinition
from server.app.pipelines.scheduler import downstream_nodes


def clear_rerun_outputs(
    definition: PipelineDefinition,
    node_key: str,
    artifact_dir: Path,
) -> list[str]:
    """Delete declared outputs for node_key and all downstream nodes.

    Each resolved output must be a regular file directly below artifact_dir.
    Returns sorted artifact names that were removed. Does not touch inputs,
    run history, logs, or session directories.
    """
    if node_key not in definition.nodes:
        raise ValueError(f"Unknown node: {node_key}")

    stale_nodes = [node_key] + downstream_nodes(definition, node_key)
    outputs: set[str] = set()
    for key in stale_nodes:
        for name in definition.nodes[key].outputs:
            outputs.add(name)

    cleared: list[str] = []
    for name in sorted(outputs):
        path = artifact_dir / name
        resolved = path.resolve()
        try:
            resolved.relative_to(artifact_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"Output path escapes artifact directory: {name}") from exc

        if resolved.is_file():
            resolved.unlink()
            cleared.append(name)

    return cleared
