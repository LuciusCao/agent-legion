"""控制台展示用的逐 runtime 状态（从 catalog 拆出，#250 预算纪律）。

``runtime_status`` 是本机探测视角；``runtime_status_with_registration``
逐行对照 Host 登记集合（executor 每次同步写入状态文件）——「实际生效」
的事实源：运行中新装/新停用的 runtime 要等重启重注册才生效，逐行标出
registered 与 pending_restart，控制台据此提示「待重启生效」（#254 评审）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from worker.runtime.catalog import RUNTIME_CATALOG, detect_installed_runtimes


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


def runtime_status_with_registration(
    disabled: Iterable[str], registered: Iterable[str] | None
) -> tuple[list[dict[str, Any]], list[str] | None]:
    """runtime_status 逐行对照 Host 登记集合（registered 为 None 表示未知）。

    返回 (rows, 排序后的登记集合或 None)。"""
    registered_set = {str(value) for value in registered} if registered is not None else None
    rows = runtime_status(disabled)
    for row in rows:
        row["registered"] = None if registered_set is None else row["runtime"] in registered_set
        row["pending_restart"] = registered_set is not None and row["enabled"] != row["registered"]
    return rows, (None if registered_set is None else sorted(registered_set))
