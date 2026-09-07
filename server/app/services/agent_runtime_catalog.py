"""Per-runtime 工具目录投影（#476）。

纯投影 service：从 ``agent_runtime`` catalog 的 adapter 声明取数据，无 IO、
无状态。数据源单一（adapter 的 ``tool_catalog``）——dispatch 期工具名校验
（#449）与 Studio 动态选项面（AgentEditor）都从同一声明取，避免「UI 能选
但 dispatch 拒」或反之。
"""

from __future__ import annotations

from typing import Any

from server.app.agent_runtime.catalog import AGENT_RUNTIMES, get_tool_catalog


class AgentRuntimeCatalogService:
    """Project the per-runtime tool catalog for the API surface."""

    def tool_catalog(self) -> dict[str, Any]:
        runtimes: dict[str, Any] = {}
        for runtime in AGENT_RUNTIMES:
            entries = get_tool_catalog(runtime)
            runtimes[runtime] = {
                "tools": [
                    {
                        "name": entry.name,
                        "tier": entry.tier,
                        "description": entry.description,
                        "parameters": entry.parameters,
                        **({"activation": entry.activation} if entry.activation else {}),
                    }
                    for entry in entries
                ]
            }
        return {"runtimes": runtimes}
