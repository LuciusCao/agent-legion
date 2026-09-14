"""Worker 二进制解析：PATH 优先、自带副本兜底（单一实现，勿另写查找逻辑）。

解析顺序：先查 PATH（#507 起 velites 在原生/裸机形态收敛为机器级单一
副本——``~/.local/bin``，由 ``scripts/ensure-velites.sh`` 无参形态按源码
指纹维护），再查自带副本目录 ``data/bin/<binary>``（仓库根相对）。data/bin
兜底只服务 Docker 外挂形态：compose 把 ``VELITES_BIN`` bind mount 到容器内
``/app/data/bin/velites``（容器内 PATH 上没有 velites），裸机存量旧副本不再
遮蔽 PATH（#507 修的静默漂移）。都找不到返回 None（调用方 fail-closed）。
启动预检（worker/runtime/preflight.py）、code 执行（worker/code_runner.py
的沙箱包装器经 shared/code_sandbox.resolve_sandbox_binary，同一目录语义）与
agent spawn（worker/execution/prepare.py）统一走 ``resolve_binary``，保证
「预检通过 = 运行时可解析到同一个二进制」。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from shared import code_sandbox


# Worker 自带二进制目录：仓库根（worker/ 包的父目录）下的 data/bin。
# data/ 不提交；#507 起原生/裸机形态不再往这里播种（唯一落点是
# ~/.local/bin），目录保留为 Docker 外挂注入点（compose bind mount 的
# target）与存量副本的兜底命中位。
# 目录事实源是 shared/code_sandbox.py 的模块属性 BUNDLED_SANDBOX_DIR（沙箱
# 解析 resolve_sandbox_binary 与本模块的 runtime 解析 resolve_binary 都经属性
# 访问读它）。#496：本模块不再 re-export 该常量——值拷贝 re-export 的
# monkeypatch 打不到任何解析函数的读取点（开发机「先 ensure-velites 再跑
# 单测」、data/bin 有 velites 时 fail-closed 测试静默变红，就是这条缝），
# 读者一律直读事实源属性；测试要隔离目录时 patch code_sandbox 一侧，即
# 同时隔离 runtime 解析与沙箱解析。
def resolve_binary(binary: str) -> str | None:
    """解析二进制绝对路径：PATH 优先，自带副本（data/bin/）兜底。

    PATH 命中即返回（机器级被维护副本）；自带副本必须存在且可执行才命中
    （Docker 挂载形态）；都找不到时返回 None。读取点走
    code_sandbox.BUNDLED_SANDBOX_DIR 属性访问（#496：与沙箱解析同一
    事实源，单一 patch 两侧生效）。"""
    resolved = shutil.which(binary)
    if resolved:
        return resolved
    bundled = Path(code_sandbox.BUNDLED_SANDBOX_DIR) / binary
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return None
