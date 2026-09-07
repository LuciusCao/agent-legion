"""Runtime adapter 与 execution / tool 契约结构（issue #75；tool catalog #476）。

``ExecutionContract`` 是 runtime 级的 execution 键声明：provider/model/thinking
的语义随 runtime 漂移（pi/velites 是平台连接选择器；未来的 runtime 各自
声明），各 adapter 声明自己支持哪些键、哪些必填；「不支持却配置了非空值」
fail-fast（校验在 ``execution.py``，dispatch 与 claim 重解析共用，
EXEC-RUNTIME-DISPATCH-001）。

``ToolCatalogEntry`` 是 runtime 级的工具目录条目（#476）：每个 runtime 声明
自己提供哪些工具、各自的 tier。同名工具交集不是契约——read/write/bash
同时出现在多个 runtime 只是命名巧合，参数 schema 与行为语义不保证一致，
description/parameters 必须随 runtime 走。velites 的目录是静态常量，与
``velites tools list --json`` 输出全等（跨二进制契约测试钉住）；pi 是外部
runtime，无法动态发现，按实测静态登记。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecutionKeyRule:
    """一个 execution 键的 runtime 级规则（required=解析链必须给出非空值；semantics 审计用）。"""

    required: bool
    semantics: str


@dataclass(frozen=True)
class ExecutionContract:
    """runtime 支持的 manifest execution 键（⊆ provider/model/thinking）；表外键配了非空值即 fail-fast。"""

    keys: Mapping[str, ExecutionKeyRule]


@dataclass(frozen=True)
class ToolCatalogEntry:
    """一个工具的 runtime 级目录条目（#476）。

    tier 三档：``default`` 进默认集（预选中可取消）；``opt-in`` 显式开启；
    ``forced`` 不是用户选择——harness 在激活条件（``activation``，CLI flag
    名）成立时强制启用，不在用户可选集内，UI 渲染为锁定行。
    """

    name: str
    tier: str  # "default" | "opt-in" | "forced"
    description: str = ""
    parameters: dict = field(default_factory=dict)
    activation: str | None = None

    @property
    def selectable(self) -> bool:
        """是否进入用户可选集（dispatch 校验与 Studio 勾选面都用它）。"""
        return self.tier != "forced"


@dataclass(frozen=True)
class RuntimeAdapter:
    """一个 agent runtime 的 Host 侧接入点。

    ``build_command`` 签名统一为
    ``(manifest, *, skill_dir, session_dir, session_name, prompt_file, prompt_instruction) -> list[str]``
    （原 ``build_command_for_flavor`` 分发后的 kwargs 全集）；路径占位符由
    调用方（pi_protocol.render_command_spec）注入，adapter 不反向 import
    pi_protocol。

    ``tool_catalog`` 是该 runtime 的工具目录（#476）：目录数据源单一——
    dispatch 期工具名校验（#449）与 Studio 动态选项面都从它取，避免
    「UI 能选但 dispatch 拒」或反之。
    """

    name: str
    binary: str
    build_command: Callable[..., list[str]]
    execution: ExecutionContract
    tool_catalog: tuple[ToolCatalogEntry, ...] = ()
