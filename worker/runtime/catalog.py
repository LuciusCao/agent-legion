"""Worker agent runtime 目录与本机探测。

支持全集 = 本 Worker 版本可承接的 agent runtime。声明语义（issue #254）：
不再由用户手工勾选启用，而是每次读取配置时按二进制解析（自带副本
``data/bin`` 优先、PATH 兜底，统一走 ``worker/binary_resolution.py``）
探测本机已安装的 runtime，**默认全部启用**；``disabled_runtimes`` 反选
停用（装了但刻意不接的场景）。生效声明 = 探测结果 − 停用集合。

元数据集中在 RUNTIME_CATALOG：启动预检（worker/runtime/preflight.py 的
code 守卫）、模型发现（worker/runtime/models.py 的 adapter 键）、配置
校验（worker/config_store.py）与控制台的 runtime_status 展示共用同一份
定义，保证「控制台看到的 = 注册声明的」。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from worker.binary_resolution import resolve_binary

RUNTIME_CATALOG: dict[str, dict[str, Any]] = {
    "velites": {
        "name": "Velites",
        "description": "内置 harness；code 节点的沙箱执行也依赖它。",
        "binaries": ("velites",),
        "install_hint": "安装 velites（仓库内部署可执行 scripts/ensure-velites.sh --dest data/bin 构建自带副本，或放入 PATH）",
    },
    "pi": {
        "name": "Pi",
        "description": "外部 Pi runtime；与 Velites 平级，按 Agent 定义选用。",
        "binaries": ("pi",),
        "install_hint": "安装 pi 到 PATH（或 data/bin/）后重启 Worker",
    },
    "openclaw": {
        "name": "OpenClaw",
        "description": "外部 OpenClaw runtime（当前版本尚未开放任务派发）。",
        "binaries": ("openclaw",),
        "install_hint": "安装 openclaw 到 PATH（或 data/bin/）后重启 Worker",
    },
}

SUPPORTED_RUNTIMES: tuple[str, ...] = tuple(RUNTIME_CATALOG)


def detect_installed_runtimes() -> dict[str, str]:
    """探测本机已安装的 runtime，返回 {runtime: 解析到的二进制路径}。"""
    installed: dict[str, str] = {}
    for runtime, meta in RUNTIME_CATALOG.items():
        for candidate in meta["binaries"]:
            resolved = resolve_binary(candidate)
            if resolved:
                installed[runtime] = resolved
                break
    return installed


def effective_runtimes(
    disabled: Iterable[str], installed: Mapping[str, str] | None = None
) -> list[str]:
    """生效声明集合：已安装 − 停用，排序稳定。"""
    available = set(installed) if installed is not None else set(detect_installed_runtimes())
    return sorted(available - {str(value) for value in disabled})


def runtime_status(
    disabled: Iterable[str], installed: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    """控制台展示用的逐 runtime 状态（按 catalog 顺序）。"""
    detected = dict(installed) if installed is not None else detect_installed_runtimes()
    disabled_set = {str(value) for value in disabled}
    rows: list[dict[str, Any]] = []
    for runtime, meta in RUNTIME_CATALOG.items():
        binary = detected.get(runtime)
        rows.append(
            {
                "runtime": runtime,
                "name": meta["name"],
                "description": meta["description"],
                "installed": binary is not None,
                "binary": binary,
                "enabled": binary is not None and runtime not in disabled_set,
                "install_hint": meta["install_hint"],
            }
        )
    return rows


def resolve_config_runtimes(config: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """从原始配置解析 (disabled_runtimes, 生效 runtimes)。

    config_store（状态副本读写）与 executor 子进程（启动时读同一状态文件）
    共用同一入口，保证「服务看到的 = 子进程声明的」。旧版 opt-in `runtimes`
    键一次性迁移为补集停用（升级后 claim 行为不变）；生效值 = 探测 − 停用。"""
    disabled_raw = config.get("disabled_runtimes")
    if disabled_raw is None and isinstance(config.get("runtimes"), list):
        legacy = {str(value) for value in config["runtimes"]}
        disabled_raw = sorted(set(SUPPORTED_RUNTIMES) - legacy)
    if disabled_raw is None:
        disabled_raw = []
    if not isinstance(disabled_raw, list):
        raise ValueError("disabled_runtimes 必须是列表")
    disabled = sorted({str(value) for value in disabled_raw})
    if any(value not in SUPPORTED_RUNTIMES for value in disabled):
        raise ValueError(f"disabled_runtimes 只能是 {', '.join(SUPPORTED_RUNTIMES)} 中的值")
    return disabled, effective_runtimes(disabled)
