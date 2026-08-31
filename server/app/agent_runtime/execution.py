"""manifest ``execution`` 块解析与 ExecutionContract 校验（EXEC-RUNTIME-DISPATCH-001；dispatch/claim 共用）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from server.app.agent_runtime.catalog import get_adapter, not_implemented_message

if TYPE_CHECKING:
    from server.app.workflows.schema import WorkflowNode

# Retired workflows.pi.timeout_seconds (yaml governance): the execution
# timeout is a product constant now, not configuration.
EXECUTION_TIMEOUT_SECONDS = 1800

# manifest execution 块里契约管辖的全部键；契约表外的键视为 runtime 不支持。
EXECUTION_KEYS: tuple[str, ...] = ("provider", "model", "thinking")

# 必填缺失的可行动报错（指向 Studio / workflow 顶层 execution 默认）。
_REQUIRED_DETAILS = {
    "provider": "requires a provider: set the node execution provider "
    "in Studio or a workflow top-level execution default",
    "model": "requires a model: set the node execution model "
    "in Studio or a workflow top-level execution default",
}


def resolve_execution_chain(*sources: Mapping[str, Any]) -> dict[str, Any]:
    """claim 重解析链：按来源优先级对每个契约管辖键取第一个非空值。"""
    return {key: next((s[key] for s in sources if s.get(key)), "") for key in EXECUTION_KEYS}


def validate_execution_contract(
    *, node_key: str, runtime: str, values: Mapping[str, Any]
) -> dict[str, str]:
    """校验并归一化 execution 值（空 = 未配置）；契约外键非空 / 必填缺失 → fail-fast。"""
    adapter = get_adapter(runtime)
    if not adapter.implemented:
        raise ValueError(not_implemented_message(runtime))
    contract = adapter.execution
    resolved = {key: str(values.get(key) or "") for key in EXECUTION_KEYS}
    for key in EXECUTION_KEYS:
        if key not in contract.keys and resolved[key]:
            raise ValueError(
                f"node {node_key} configures execution.{key}, which agent runtime {runtime!r} "
                f"does not support (supported execution keys: {', '.join(contract.keys)})"
            )
    for key, rule in contract.keys.items():
        if rule.required and not resolved[key]:
            detail = _REQUIRED_DETAILS.get(key, f"requires execution.{key} for {runtime!r}")
            raise ValueError(f"node {node_key} {detail}")
    return resolved


def resolve_execution(node: WorkflowNode, runtime: str) -> dict[str, Any]:
    """Resolve the manifest ``execution`` block (strict node-only source, contract-checked).

    The node execution seen here already carries the loader-merged workflow
    top-level defaults (workspace-level defaults retired at schema v64).
    """
    adapter = get_adapter(runtime)
    resolved = validate_execution_contract(
        node_key=node.key, runtime=runtime, values=asdict(node.execution)
    )
    return {
        "binary": adapter.binary,
        "provider": resolved["provider"],
        "model": resolved["model"],
        "thinking": resolved["thinking"],
        "timeout_seconds": EXECUTION_TIMEOUT_SECONDS,
        "no_sandbox": False,
    }
