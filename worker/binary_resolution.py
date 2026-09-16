"""Worker 二进制解析：自带副本优先，PATH 兜底（单一实现，勿另写查找逻辑）。

解析顺序：先查 Worker 自带副本 ``data/bin/<binary>``（仓库根相对，Docker
镜像外的裸机部署经 ``scripts/ensure-velites.sh --dest data/bin`` 按平台
安置），再查 PATH；都找不到返回 None（调用方 fail-closed）。启动预检
（worker/runtime/preflight.py）、code 执行（worker/code_runner.py 的沙箱
包装器经 shared/code_sandbox.resolve_sandbox_binary，同一目录语义）与
agent spawn（worker/execution/prepare.py）统一走 ``resolve_binary``，
保证「预检通过 = 运行时可解析到同一个二进制」。
"""

from __future__ import annotations

import os
import shutil

# Worker 自带二进制目录：仓库根（worker/ 包的父目录）下的 data/bin。
# data/ 不提交，二进制按平台构建后安置于此（见 ensure-velites.sh --dest）。
# 单一事实源在 shared/code_sandbox.py（沙箱解析与 runtime 解析共用该目录）；
# 此处经模块属性访问读取（#496：值导入的 re-export 打不到
# resolve_sandbox_binary 函数体里的原常量——开发机 data/bin 有 velites 时
# fail-closed 测试静默失真），monkeypatch code_sandbox.BUNDLED_SANDBOX_DIR
# 即同时改变两侧解析的目录。名字 re-export 维持既有 import 路径。
from shared import code_sandbox
from shared.code_sandbox import BUNDLED_SANDBOX_DIR as BUNDLED_BINARY_DIR

__all__ = ["BUNDLED_BINARY_DIR", "resolve_binary"]


def resolve_binary(binary: str) -> str | None:
    """解析二进制绝对路径：自带副本（data/bin/）优先，PATH 兜底。

    自带副本必须存在且可执行；找不到时返回 None。"""
    bundled = code_sandbox.BUNDLED_SANDBOX_DIR / binary
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    return shutil.which(binary)
