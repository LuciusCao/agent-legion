"""Worker 二进制解析：PATH 优先、自带副本兜底（单一实现，勿另写查找逻辑）。

解析顺序：先查 PATH（#507 起 velites 在原生/裸机形态收敛为机器级单一
副本——``~/.local/bin``，由 ``scripts/ensure-velites.sh`` 无参形态按源码
指纹维护），再查自带副本目录 ``data/bin/<binary>``（仓库根相对）。data/bin
兜底只服务 Docker 外挂形态：compose 把 ``VELITES_BIN`` bind mount 到容器内
``/app/data/bin/velites``（容器内 PATH 上没有 velites），裸机存量旧副本
不再遮蔽 PATH（双副本时代 data/bin 永久优先且无人维护，prod-up 刷新的
PATH 副本永不生效——#507 修的静默漂移）。都找不到返回 None（调用方
fail-closed）。启动预检（worker/runtime/preflight.py）、code 执行
（worker/code_runner.py 的沙箱包装器经 shared/code_sandbox.resolve_sandbox_binary，
同一目录语义）与 agent spawn（worker/execution/prepare.py）统一走
``resolve_binary``，保证「预检通过 = 运行时可解析到同一个二进制」。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Worker 自带二进制目录：仓库根（worker/ 包的父目录）下的 data/bin。
# data/ 不提交；#507 起原生/裸机形态不再往这里播种（唯一落点是
# ~/.local/bin），目录保留为 Docker 外挂注入点（compose bind mount 的
# target）与存量副本的兜底命中位。
# 单一事实源在 shared/code_sandbox.py（沙箱解析与 runtime 解析共用该目录，
# mock 任一 re-export 都改变同一常量）；此处 re-export 维持既有 import 路径。
from shared.code_sandbox import BUNDLED_SANDBOX_DIR as BUNDLED_BINARY_DIR


def resolve_binary(binary: str) -> str | None:
    """解析二进制绝对路径：PATH 优先，自带副本（data/bin/）兜底。

    PATH 命中即返回（机器级被维护副本）；自带副本必须存在且可执行才命中
    （Docker 挂载形态）；都找不到时返回 None。"""
    resolved = shutil.which(binary)
    if resolved:
        return resolved
    bundled = Path(BUNDLED_BINARY_DIR) / binary
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return None
