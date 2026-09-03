"""Worker runtime startup: binary preflight followed by model discovery."""

from __future__ import annotations

import os
from typing import Any

from worker.runtime.catalog import probe_runtime_versions, resolve_config_runtimes
from worker.runtime.models import discover_effective_models
from worker.runtime.preflight import (
    EXPECT_RUNTIMES_ENV,
    parse_expect_runtimes,
    preflight_error,
)


def prepare_runtime_models(config: dict[str, Any], *, code_concurrency: int = 0) -> str | None:
    """Resolve runtime declarations, validate binaries, inject effective models.

    executor 子进程直接读状态文件（原始 yaml），生效 runtimes 在这里按
    catalog 语义现算（探测 − disabled_runtimes，含旧 opt-in 键迁移），与
    config_store 读取路径共用同一入口（issue #254）。环境变量
    ``AGENT_WORKER_EXPECT_RUNTIMES`` 的期望 runtime 守卫（issue #381）也
    在这里并入预检：值非法（未知 runtime）与探测不到都 fail-fast。

    期望 runtime 的**模型发现失败**同样 fail-fast（codex P1 on #384）：
    错误架构/损坏的二进制能通过存在性探测（is_file + X_OK），却在执行时
    以 exec format error 失败——若只按软告警处理，worker 会健康注册但
    零容量，正是守卫要消灭的静默形态。非期望 runtime 的发现失败维持
    软告警（该 runtime 不领取任务）。"""
    expect_runtimes = None
    try:
        expect_runtimes = parse_expect_runtimes(os.environ.get("AGENT_WORKER_EXPECT_RUNTIMES"))
    except ValueError as exc:
        return f"Agent Worker 启动预检失败：{exc}"
    try:
        disabled, runtimes = resolve_config_runtimes(config)
    except ValueError as exc:
        return f"Agent Worker 启动预检失败：{exc}"
    config["disabled_runtimes"] = disabled
    config["runtimes"] = runtimes
    # subagent P2-1 on #384：守卫必须对**生效集合**（探测 − 停用）生效，
    # 不只对已安装集合——旧版 opt-in `runtimes` 键迁移（catalog）可把
    # velites 静默转入 disabled_runtimes，只查安装会让「守卫绿灯 + 零
    # runtime 注册」复活。合法路径是二选一：从期望集合移除该值，或从
    # disabled_runtimes 取消停用；错误信息把这两条路都说清楚。
    if expect_runtimes and (disabled_effective := sorted(set(expect_runtimes) & set(disabled))):
        return (
            f"Agent Worker 启动预检失败：{EXPECT_RUNTIMES_ENV}（{', '.join(expect_runtimes)}）"
            f"与 disabled_runtimes 冲突：{', '.join(disabled_effective)} 已安装但被停用"
            "（旧版 `runtimes` opt-in 键迁移也会转入停用集合）。修正二选一："
            f"从 {EXPECT_RUNTIMES_ENV} 移除该 runtime，或从 disabled_runtimes 取消停用后重启"
        )
    error = preflight_error(code_concurrency=code_concurrency, expect_runtimes=expect_runtimes)
    if error is not None:
        return error
    effective, discovery_errors = discover_effective_models(config)
    if expect_runtimes and (failed := sorted(set(expect_runtimes) & set(discovery_errors))):
        details = "；".join(f"{runtime}: {discovery_errors[runtime]}" for runtime in failed)
        return (
            "Agent Worker 启动预检失败：期望 runtime（"
            f"{', '.join(expect_runtimes)}）模型发现失败，该 runtime 实际不可用：{details}。"
            "常见原因：二进制架构与主机/镜像不一致（exec format error）、二进制损坏或"
            "运行依赖缺失；修复后重启。非期望 runtime 的发现失败不影响启动"
            "（该 runtime 不领取 Agent 任务）"
        )
    for runtime, detail in discovery_errors.items():
        print(
            f"运行时 {runtime!r} 模型发现失败，该 runtime 不会领取 Agent 任务：{detail}",
            flush=True,
        )
    config["models"] = effective
    # #381 版本握手：注册 payload 携带生效 runtime 的 --version（host 侧
    # 日志可查「worker 代码 × velites 版本」矩阵，外挂后漂移可观测）。
    config["runtime_versions"] = probe_runtime_versions(runtimes)
    return None
