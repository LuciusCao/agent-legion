"""Strict resolution of the manifest ``execution`` block.

Split out of ``dispatch.py`` (issue 287): the resolver is the runtime-pinning
rule (which binary, which timeout constant) that both the manifest builder
and the validation surface depend on, while ``dispatch.py`` keeps only the
enqueue pipeline (bundle staging, pool, broker handoff). The implementation
lives in ``server/app/agent_runtime/execution.py`` (issue #75): the runtime
catalog pins the binary and command builder, and the adapter's
``ExecutionContract`` is validated (required keys / unsupported keys fail
fast). This module stays the import site for existing callers.
"""

from __future__ import annotations

from typing import Any

from server.app.agent_runtime.execution import resolve_execution
from server.app.workflows.schema import WorkflowNode

__all__ = ["resolve_execution_block"]


def resolve_execution_block(node: WorkflowNode, runtime: str) -> dict[str, Any]:
    """Resolve the manifest ``execution`` block (strict, node-only source)."""
    return resolve_execution(node, runtime)
