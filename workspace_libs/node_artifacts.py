"""Artifact store for the node SDK (split from ``node_sdk`` for size budget).

A node's artifacts are the files it reads and writes under ``job_dir`` — the
single IO surface between DAG nodes. ``NodeContext.artifacts`` is an instance
of the store below; nodes should never hand-roll ``read_text``/``json.loads``
against ``job_dir`` themselves.

Layering rule: standard library only, no ``server.app.*`` imports (same
execution-plane constraint as ``node_sdk``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from workspace_libs.node_sdk import NodeContext


class ArtifactStore:
    """Uniform access to the files a node reads and writes under ``job_dir``."""

    def __init__(self, context: NodeContext) -> None:
        self._context = context

    @property
    def dir(self) -> Path:
        return self._context.job_dir

    def path(self, name: str) -> Path:
        return self._context.job_dir / name

    def read_text(self, name: str) -> str:
        return self.path(name).read_text(encoding="utf-8")

    def read_json(self, name: str) -> Any:
        return json.loads(self.read_text(name))

    def read_json_object(self, name: str) -> dict[str, Any]:
        """Read *name* and require a JSON object (dict) payload."""
        path = self.path(name)
        if not path.is_file():
            raise ValueError(f"Missing input: {name}")
        content = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(content, dict):
            raise ValueError(f"Invalid content in {name}")
        return content

    def write_text(self, name: str, text: str) -> Path:
        # Writing is the natural stage-commit boundary: checkpoint here so
        # cancelled executions stop before producing partial output batches.
        self._context.checkpoint()
        self._context.job_dir.mkdir(parents=True, exist_ok=True)
        path = self.path(name)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, name: str, payload: Any) -> Path:
        return self.write_text(name, json.dumps(payload, ensure_ascii=False, indent=2))
