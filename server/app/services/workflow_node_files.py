"""Read repo-tracked workflow node code files.

Backend for the DAG node code viewer: exposes the tracked Python files under
``workflow_nodes/`` (each exposing a module-level ``run``) plus the executor
capabilities that reference them. Reads are whitelist-validated; node code
changes land through git review + CI (EXEC-CODE-001), so this module has no
write path.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from server.app.executors.config import CodeExecutorConfig, ExecutorConfig

WORKFLOW_NODES_DIR = "workflow_nodes"

# server/app/services/workflow_node_files.py -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]


class NodeFileError(ValueError):
    """Invalid node file request (path or content); routes translate it to 422."""


def workflow_nodes_dir(root_dir: Path | None = None) -> Path:
    return (root_dir if root_dir is not None else REPO_ROOT) / WORKFLOW_NODES_DIR


def resolve_node_path(nodes_dir: Path, name: str) -> Path:
    """Resolve a URL-supplied node file name to a file inside ``nodes_dir``.

    Accepts the file name relative to ``workflow_nodes/`` (optionally with the
    ``workflow_nodes/`` prefix). Raises ``NodeFileError`` for anything outside
    the whitelist and ``FileNotFoundError`` when the file does not exist.
    """
    relative = PurePosixPath(name)
    if relative.is_absolute():
        raise NodeFileError(f"node file path must be relative: {name!r}")
    parts = relative.parts
    if ".." in parts:
        raise NodeFileError(f"node file path must not contain '..': {name!r}")
    if parts and parts[0] == WORKFLOW_NODES_DIR:
        parts = parts[1:]
    if len(parts) != 1:
        raise NodeFileError(f"node file must be a single file name: {name!r}")
    filename = parts[0]
    if not filename.endswith(".py"):
        raise NodeFileError(f"node file must be a Python file: {name!r}")
    if filename.startswith("__"):
        raise NodeFileError(f"node file name must not start with '__': {name!r}")
    root = nodes_dir.resolve()
    resolved = (root / filename).resolve()
    if resolved.parent != root:
        raise NodeFileError(f"node file must stay inside {WORKFLOW_NODES_DIR}/: {name!r}")
    if not resolved.is_file():
        raise FileNotFoundError(f"node file not found: {WORKFLOW_NODES_DIR}/{filename}")
    return resolved


def read_node_file(nodes_dir: Path, name: str) -> tuple[str, str]:
    """Return ``(repo-relative path, content)`` for an existing node file."""
    path = resolve_node_path(nodes_dir, name)
    return _display_path(path), path.read_text(encoding="utf-8")


def referencing_capabilities(
    executor_definitions: Mapping[str, ExecutorConfig], path: str
) -> list[dict[str, str]]:
    """Code-executor capabilities whose ``path`` points at this node file."""
    target = PurePosixPath(path)
    references: list[dict[str, str]] = []
    for executor_id, definition in executor_definitions.items():
        if not isinstance(definition, CodeExecutorConfig):
            continue
        for capability, cap_config in definition.capabilities.items():
            if PurePosixPath(cap_config.path) == target:
                references.append({"executor_id": executor_id, "capability": capability})
    return sorted(references, key=lambda item: (item["executor_id"], item["capability"]))


def _display_path(path: Path) -> str:
    return f"{WORKFLOW_NODES_DIR}/{path.name}"
