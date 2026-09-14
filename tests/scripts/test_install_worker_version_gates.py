"""install-worker.sh 版本门槛的轻量契约（issue #489 P2）。

脚本钉住的默认版本（当前 0.7.0）与「token 自动内嵌」的能力版本（0.8.0）
之间存在发布窗口：compose/镜像还没有内嵌判定时，成功提示不得宣称「token
已内嵌页面」。这里钉住两件事：

1. 版本比较正则与门槛行为本身（0.7.x 手动 / 0.8.0+ 与 -rN 后缀自动内嵌 /
   非数字 tag 保守按手动）——把 sed 提取与整数比较从脚本里逐字复制出来
   跑同一组输入，脚本逻辑漂移（正则/门槛被改）时这里红；
2. 脚本源码里门槛常数 8 与两种文案共存——只留一种文案（回归 PR #489 的
   原始 bug 形态：文案无条件宣称已内嵌）时这里红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "install-worker.sh"


def _script_version_gate(version: str) -> str:
    """跑脚本同款的 sed 提取 + 整数门槛，返回 embedded / manual。"""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "s/^\\([0-9]*\\)\\.\\([0-9]*\\).*/\\1 \\2/p" in source, (
        "install-worker.sh 缺版本比较 sed 正则（token 文案门槛被改？）"
    )
    import subprocess

    extracted = subprocess.run(
        ["sed", "-n", "s/^\\([0-9]*\\)\\.\\([0-9]*\\).*/\\1 \\2/p"],
        input=version,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not extracted:
        return "manual"
    major, minor = extracted.split()
    return "embedded" if int(major) > 0 or int(minor) >= 8 else "manual"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.7.0", "manual"),  # 当前默认钉点：发布窗口，页面仍要求手动 token
        ("0.6.1", "manual"),
        ("0.8.0", "embedded"),  # 内嵌判定的首个发布版本
        ("0.8.0-r1", "embedded"),  # -rN 重发后缀被 `.*` 吸收，基础版本比较不受影响
        ("0.9.3", "embedded"),
        ("1.0.0", "embedded"),
        ("latest", "manual"),  # 非数字 tag：保守走手动文案（永不错）
    ],
)
def test_version_gate_classification(version: str, expected: str) -> None:
    assert _script_version_gate(version) == expected


def test_success_hint_carries_both_branches_and_pin_below_gate() -> None:
    """脚本必须同时保留两种文案，且默认版本钉点在内嵌门槛之下。"""
    source = SCRIPT.read_text(encoding="utf-8")
    # 两种文案分支共存（只留 embedded 分支 = PR #489 原始 bug 的回归形态）
    assert "已内嵌页面" in source, "install-worker.sh 缺 0.8.0+ 的「token 已内嵌」文案分支"
    assert "仍需手动输入 token" in source, "install-worker.sh 缺 <0.8.0 的「仍需手动输入」文案分支"
    assert "0.8.0" in source, "install-worker.sh 缺内嵌能力的版本说明（用户无法得知门槛）"
    # 默认钉点低于门槛：文案必须以 manual 分支为默认路径（发布窗口的真实状态）
    pin = re.search(r'WORKER_VERSION_DEFAULT="([0-9]+\.[0-9]+\.[0-9]+)"', source)
    assert pin, (
        "install-worker.sh 缺 WORKER_VERSION_DEFAULT 钉点（check_release_pins 门禁会另行拦截）"
    )
    major, minor, _patch = pin.group(1).split(".")
    assert (int(major), int(minor)) < (0, 8), (
        "默认钉点已到 0.8.0+：本测试对「发布窗口」的假设过期，请更新参数表里的"
        " 0.7.0 manual 期望并复核两分支文案是否仍必要"
    )
