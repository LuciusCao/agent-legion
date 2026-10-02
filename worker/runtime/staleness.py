"""#831 velites 副本新鲜度对账（Worker 侧组合面，软告警）。

对账核心（指纹校验、stamp 读取、git 探测、文案构建、总兜底）在
``shared/velites_staleness.py``——Host 侧启动钩子（server/app/main.py）
不得 import worker 包，两侧共用同一核心。本模块组合 Worker 进程的两个
消费面：

1. agent runtime 面：``worker/binary_resolution.resolve_binary("velites")``
   （自带副本 data/bin 优先、PATH 兜底——注册声明与 Agent 任务执行用）；
2. code 沙箱面：``shared.code_sandbox.resolve_sandbox_binary``（候选序
   velites-sandbox 优先——#835 四轮 codex 评审的主战场，此前对账恰好
   漏掉它：包装器漂移时 Worker 启动日志无声）。

同一二进制被两面解析时按路径去重、角色合并（核心行为）。preflight.py
按其 file_budget 豁免条款「第三个守卫出现时拆分」经
``velites_staleness_warning`` 名字 re-export 维持导入路径。
"""

from __future__ import annotations

from shared.code_sandbox import resolve_sandbox_binary
from shared.velites_staleness import reconcile_velites_copies
from worker.binary_resolution import resolve_binary


def velites_staleness_warning() -> str | None:
    """指纹对账（软告警）：Worker 解析到的全部 velites 家族二进制 vs 源码。

    漂移路径各返回一条告警文案（换行连接）；不可对账或对账过程任何失败
    返回 None——不变量见 shared/velites_staleness.py 的模块 docstring
    （软告警绝不阻断启动）。"""
    try:
        warnings = reconcile_velites_copies(
            [
                ("agent runtime（Worker 注册声明与任务执行）", resolve_binary("velites")),
                ("code 沙箱（Host 与 Worker 的 code 节点）", resolve_sandbox_binary()),
            ]
        )
    except Exception:  # noqa: BLE001
        # #204 broad-except audit: 核心已带总兜底，这里是接线层的冗余防线
        # （resolve_* 自身抛出未枚举异常的形态）——吞掉即「不可对账」语义，
        # 启动路径零依赖软告警，下次启动自然再试。
        return None
    return "\n".join(warnings) or None
