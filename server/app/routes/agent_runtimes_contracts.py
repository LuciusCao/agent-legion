from typing import Any

from pydantic import BaseModel, Field


class RuntimeToolEntry(BaseModel):
    """一个工具的目录条目（#476）。

    ``tier`` 三档：``default``（预选中可取消）/ ``opt-in``（显式开启）/
    ``forced``（非用户选择，激活条件成立时 harness 强制启用，UI 渲染锁定行）。
    ``activation`` 仅 forced 档出现（激活条件的 CLI flag 名）；路由以
    ``response_model_exclude_none`` 序列化，其余档不携带该键——与 velites
    侧 ``tools list --json`` 的 skip_serializing_if 对齐。
    """

    name: str
    tier: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    activation: str | None = None


class RuntimeTools(BaseModel):
    """单个 runtime 的工具目录（嵌套在响应的 runtimes 映射里）。"""

    tools: list[RuntimeToolEntry] = Field(default_factory=list)


class AgentRuntimesResponse(BaseModel):
    """Per-runtime 工具目录（#476）：按 runtime 嵌套，不做扁平全局清单。

    同名工具交集不是契约——description/parameters 随 runtime 走，消费方
    （Studio）不得借交集建立跨 runtime 统一语义。
    """

    runtimes: dict[str, RuntimeTools]
