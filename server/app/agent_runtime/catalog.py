"""Agent runtime 目录：Host 侧 runtime 全集的单一事实来源（issue #75）。

``AGENT_RUNTIMES`` 是全集唯一来源；``AgentDefinition.runtime`` 的三处
Literal（agent_catalog/definition.py、routes/agent_definition_contracts.py、
routes/agent_catalog_contracts.py，保持字面写法以保住 OpenAPI/前端类型）、
Worker 注册白名单（agent_control/registry.py）、Worker 侧
``worker/runtime/catalog.py`` 与 ``worker/cli_args.py`` 的 choices 由
``tests/agent_runtime/test_runtime_catalog.py`` 钉住全等。

pi / velites / openclaw 均已实现（openclaw 自阶段 3 接入，adapter 在
``agent_runtime/openclaw.py``）。``implemented=False`` 机制保留给未来
先注册后实现的 runtime。
"""

from __future__ import annotations

from server.app.agent_runtime.adapter import RuntimeAdapter
from server.app.agent_runtime.openclaw import ADAPTER as _OPENCLAW_ADAPTER
from server.app.agent_runtime.pi import ADAPTER as _PI_ADAPTER
from server.app.agent_runtime.velites import ADAPTER as _VELITES_ADAPTER

__all__ = ["AGENT_RUNTIMES", "get_adapter", "not_implemented_message"]

# 与三处 Literal 的书写顺序一致（集合相等由一致性测试钉住，顺序只影响文案）。
_ADAPTERS: tuple[RuntimeAdapter, ...] = (_PI_ADAPTER, _OPENCLAW_ADAPTER, _VELITES_ADAPTER)

AGENT_RUNTIMES: tuple[str, ...] = tuple(adapter.name for adapter in _ADAPTERS)


def get_adapter(runtime: str) -> RuntimeAdapter:
    """按 runtime 名取 adapter；未知 runtime fail-fast，文案列全集。"""
    for adapter in _ADAPTERS:
        if adapter.name == runtime:
            return adapter
    raise ValueError(
        f"unknown agent runtime {runtime!r} (known runtimes: {', '.join(AGENT_RUNTIMES)})"
    )


def not_implemented_message(runtime: str) -> str:
    """已注册但未实现 runtime 的 fail-fast 文案（支持集从 catalog 拼）。"""
    supported = ", ".join(adapter.name for adapter in _ADAPTERS if adapter.implemented)
    return f"Agent runtime {runtime!r} is not implemented yet (supported runtimes: {supported})"
