from pathlib import Path

import yaml


def check_pipeline_definitions(root: Path) -> list[str]:
    errors: list[str] = []
    pipelines_dir = root / "config/pipelines"
    if not pipelines_dir.is_dir():
        return errors
    for path in sorted(pipelines_dir.glob("*.yaml")):
        relative_path = path.relative_to(root).as_posix()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{relative_path}: invalid YAML ({exc})")
            continue
        if not isinstance(raw, dict):
            errors.append(f"{relative_path}: pipeline definition must be a mapping")
            continue
        if "concurrency" in raw:
            errors.append(
                f"{relative_path}: top-level 'concurrency' was removed; "
                "configure Executor limits at Workspace level"
            )
        nodes = raw.get("nodes")
        if not isinstance(nodes, dict):
            errors.append(f"{relative_path}: pipeline nodes must be a mapping")
            continue
        for node_key, node in nodes.items():
            if not isinstance(node, dict):
                errors.append(f"{relative_path}: node {node_key} must be a mapping")
                continue
            if "runner" in node:
                errors.append(
                    f"{relative_path}: node {node_key}: field 'runner' was removed; "
                    "bind a compatible Executor in Workspace settings"
                )
            if "agent" in node:
                errors.append(
                    f"{relative_path}: node {node_key}: field 'agent' was removed; "
                    "invocation details belong to Executor capabilities"
                )
            capability = node.get("capability", "")
            if not isinstance(capability, str) or not capability:
                errors.append(
                    f"{relative_path}: node {node_key} must declare a non-empty capability"
                )
    return errors
