"""manifest ``tools`` 的 per-runtime 校验（#449/#476；dispatch/claim 共用）。

数据源与 Studio 动态选项面同一份（catalog adapter 的 ``tool_catalog``），
避免「UI 能选但 dispatch 拒」或反之。forced 档不在用户可选集内（validate
经 ``--require-output`` 由 harness 强制激活，与 ``--tools`` 无关），校验只
覆盖可选集。
"""

from __future__ import annotations

from server.app.agent_runtime.catalog import get_tool_catalog
from server.app.agent_runtime.tool_catalog import selectable_tool_names


def validate_tools(*, node_key: str, runtime: str, tools: list[str] | tuple[str, ...]) -> list[str]:
    """校验节点/定义声明的工具名并按目录顺序归一化；未知工具 fail-fast。

    空列表合法（= 不开任何工具）。forced 档工具名被静默剔除——老定义里
    显式写了 ``validate`` 的按 no-op 对待（与 velites 侧 ``--tools
    validate`` 的向后兼容语义一致：激活与否由 ``--require-output`` 决定，
    与 ``--tools`` 无关）。
    """
    catalog = get_tool_catalog(runtime)
    selectable = selectable_tool_names(catalog)
    known = frozenset(entry.name for entry in catalog)
    unknown = sorted({tool for tool in tools if tool not in known})
    if unknown:
        raise ValueError(
            f"node {node_key} selects tools not offered by agent runtime {runtime!r}: "
            f"{', '.join(unknown)} (available: {', '.join(sorted(selectable))})"
        )
    # 目录声明顺序归一化，manifest 冻结值稳定可重现。
    selected = set(tools)
    return [entry.name for entry in catalog if entry.name in selected and entry.selectable]


def manifest_tools(
    node_key: str, runtime: str, node_tools: tuple[str, ...] | list[str], fallback: tuple[str, ...]
) -> list[str]:
    """dispatch 的 tools 冻结值：节点级声明优先，回落 Agent 定义默认（#443）。

    两个来源都在同一 catalog 上校验——UI 前置校验（Studio 失效标记）与
    dispatch fail-fast（本函数）读同一数据。位置参数：dispatch 的调用点
    在 manifest 字典字面量里，单行可读。
    """
    source = list(node_tools) if node_tools else list(fallback)
    return validate_tools(node_key=node_key, runtime=runtime, tools=source)


__all__: list[str] = ["manifest_tools", "validate_tools"]
