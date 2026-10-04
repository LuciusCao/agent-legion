"""install-worker.sh 版本门槛的轻量契约（issue #489）。

「token 自动内嵌」随 worker 0.7.16 发布（且以控制面 Host 头校验为前提，
#923）；`--version` 装更旧的镜像/compose 时没有内嵌判定，成功提示不得
宣称「token 已内嵌页面」。这里钉住两件事：

1. 版本比较正则与门槛行为本身（<0.7.16 手动 / 0.7.16+ 与 -rN 后缀自动
   内嵌 / 非数字 tag 保守按手动）——把 sed 提取与整数比较从脚本里逐字
   复制出来跑同一组输入，脚本逻辑漂移（正则/门槛被改）时这里红；
2. 脚本源码里两种文案共存——只留一种文案（文案无条件宣称已内嵌）时这里红。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "install-worker.sh"
_SED_EXPR = "s/^\\([0-9][0-9]*\\)\\.\\([0-9][0-9]*\\)\\.\\([0-9][0-9]*\\).*/\\1 \\2 \\3/p"


def _script_version_gate(version: str) -> str:
    """跑脚本同款的 sed 提取 + 整数门槛，返回 embedded / manual。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert _SED_EXPR in source, "install-worker.sh 缺版本比较 sed 正则（token 文案门槛被改？）"
    assert '[ "$2" -eq 7 ] && [ "$3" -ge 16 ]' in source, "install-worker.sh 内嵌门槛被改？"
    extracted = subprocess.run(
        ["sed", "-n", _SED_EXPR],
        input=version,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not extracted:
        return "manual"
    major, minor, patch = (int(part) for part in extracted.split())
    return "embedded" if (major, minor, patch) >= (0, 7, 16) else "manual"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.7.0", "manual"),
        ("0.7.15", "manual"),
        ("0.6.1", "manual"),
        ("0.7.16", "embedded"),  # 内嵌判定的首个发布版本
        ("0.7.16-r1", "embedded"),  # -rN 重发后缀被 `.*` 吸收，基础版本比较不受影响
        ("0.8.0", "embedded"),
        ("1.0.0", "embedded"),
        ("latest", "manual"),  # 非数字 tag：保守走手动文案（永不错）
    ],
)
def test_version_gate_classification(version: str, expected: str) -> None:
    assert _script_version_gate(version) == expected


def test_success_hint_carries_both_branches() -> None:
    """脚本必须同时保留两种文案：`--version` 仍可装门槛以下的旧版本。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "已内嵌页面" in source, "install-worker.sh 缺 0.7.16+ 的「token 已内嵌」文案分支"
    assert "仍需手动输入 token" in source, "install-worker.sh 缺旧版本的「仍需手动输入」文案分支"
    assert "0.7.16" in source, "install-worker.sh 缺内嵌能力的版本说明（用户无法得知门槛）"
    # 版本够但发布地址 / 控制台地址非回环时 service 不内嵌（#923），提示走手动文案
    assert "token_embedded=exposed" in source, "install-worker.sh 缺非回环暴露面的手动文案分支"
    assert "AGENT_WORKER_CONSOLE_URL" in source, "install-worker.sh 未按控制台地址判定内嵌"
