"""Runtime adapter 与 execution 契约结构（issue #75）。

``ExecutionContract`` 是 runtime 级的 execution 键声明：provider/model/thinking
的语义随 runtime 漂移（pi/velites 是平台连接选择器，openclaw 是
``provider/model`` 组合串），各 adapter 声明自己支持哪些键、哪些必填；
「不支持却配置了非空值」fail-fast（校验在 ``execution.py``，dispatch 与
claim 重解析共用，EXEC-RUNTIME-DISPATCH-001）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass


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
class RuntimeAdapter:
    """一个 agent runtime 的 Host 侧接入点。

    ``build_command`` 签名统一为
    ``(manifest, *, skill_dir, session_dir, session_name, prompt_file, prompt_instruction) -> list[str]``
    （原 ``build_command_for_flavor`` 分发后的 kwargs 全集）；路径占位符由
    调用方（pi_protocol.render_command_spec）注入，adapter 不反向 import
    pi_protocol。``implemented=False`` = 已注册但尚未开放派发（预留机制）。
    execution 解析与命令构建都 fail-fast。
    """

    name: str
    binary: str
    build_command: Callable[..., list[str]]
    execution: ExecutionContract
    implemented: bool = True
