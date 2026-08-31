"""Strict resolution of the manifest ``execution`` block.

Split out of ``dispatch.py`` (issue 287): the resolver is the runtime-pinning
rule (which binary, which timeout constant) that both the manifest builder
and the validation surface depend on, while ``dispatch.py`` keeps only the
enqueue pipeline (bundle staging, pool, broker handoff). The rule is strict
by design — every source of provider/model is the node-level execution block
merged by the loader, and unknown runtimes fail before anything is frozen.
"""

from __future__ import annotations

from typing import Any

from server.app.workflows.schema import WorkflowNode

# Retired workflows.pi.timeout_seconds (yaml governance): the execution
# timeout is a product constant now, not configuration.
EXECUTION_TIMEOUT_SECONDS = 1800

_RUNTIME_BINARIES = {"pi": "pi", "velites": "velites"}


def resolve_execution_block(node: WorkflowNode, runtime: str) -> dict[str, Any]:
    """Resolve the manifest ``execution`` block (strict, node-only source).

    provider/model resolve from the node-level execution only — the loader
    has already merged the workflow top-level execution defaults into the
    node, so the value seen here is the effective one; either one missing
    fails the enqueue with an actionable error (agent config governance,
    workspace-level defaults retired at schema v64). thinking stays optional
    — empty means the runtime decides. The runtime pins the command builder
    (EXEC-RUNTIME-DISPATCH-001); unknown runtimes fail fast so no manifest
    is ever frozen with an unbuildable command spec.
    """
    binary = _RUNTIME_BINARIES.get(runtime)
    if binary is None:
        raise ValueError(
            f"Agent runtime {runtime!r} is not implemented yet (supported runtimes: pi, velites)"
        )
    provider = node.execution.provider
    model = node.execution.model
    if not provider:
        raise ValueError(
            f"node {node.key} requires a provider: set the node execution provider "
            "in Studio or a workflow top-level execution default"
        )
    if not model:
        raise ValueError(
            f"node {node.key} requires a model: set the node execution model "
            "in Studio or a workflow top-level execution default"
        )
    return {
        "binary": binary,
        "provider": provider,
        "model": model,
        "thinking": node.execution.thinking,
        "timeout_seconds": EXECUTION_TIMEOUT_SECONDS,
        "no_sandbox": False,
    }
