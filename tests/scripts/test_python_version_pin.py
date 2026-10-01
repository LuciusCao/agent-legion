"""Contract tests for the .python-version pin (issue #483).

#480 开发期间撞上：本机所有 worktree venv 落到 Python 3.12，而 CI
（.github/workflows/quality-gate.yml 各 backend job）用 3.13——仓库
requires-python >= 3.11 且无 .python-version 时，uv sync 落到什么版本
取决于机器上 uv 的解析结果。本地 3.12 上 test_code_child 的 sigterm 用例
非确定性失败（3.12 的 _communicate 对已关 stdin flush 抛 ValueError；绕过
后 signal handler 打断子进程 stdout 管道写时偶发永久阻塞），CI 3.13 一直
绿所以长期无人发现。

契约：.python-version 钉 3.13，且 CI workflows 的 python-version 钉点
必须与之一致——防 CI 与本地钉点再漂移（nightly-gate 一并覆盖，其 job 同样
在本地之外跑）。requires-python 保持 >=3.11 不动（收紧单独讨论）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
PIN_FILE = ROOT / ".python-version"
PIN = "3.13"

# workflows 里出现 python-version: "X.Y" 的 CI 定义文件（setup-python 的
# 输入；其它形态（如 actions 缓存 key）不属于本契约）。
_WORKFLOWS = (
    ROOT / ".github" / "workflows" / "quality-gate.yml",
    ROOT / ".github" / "workflows" / "nightly-gate.yml",
)


def test_python_version_file_pins_313() -> None:
    """.python-version 内容为 3.13（uv 自动读取；本机 uv 已有 3.13.x）。"""
    assert PIN_FILE.is_file(), ".python-version 缺失（issue #483：本地 venv 与 CI 漂移的根因）"
    assert PIN_FILE.read_text(encoding="utf-8").strip() == PIN


def test_ci_python_versions_match_the_pin() -> None:
    """CI workflows 里全部 python-version 钉点必须与 .python-version 一致。"""
    pattern = re.compile(r'python-version:\s*"(\d+\.\d+)"')
    for workflow in _WORKFLOWS:
        text = workflow.read_text(encoding="utf-8")
        pins = pattern.findall(text)
        # 每个 setup-python step 都必须显式钉版本：空列表意味着 workflow
        # 改用别的方式选 Python（如容器镜像），契约测试需跟着重新审视。
        assert pins, f"{workflow.name} 未解析到 python-version 钉点"
        mismatched = [pin for pin in pins if pin != PIN]
        assert not mismatched, (
            f"{workflow.name} 的 python-version 钉点与 .python-version ({PIN}) 漂移: {mismatched}"
        )


def test_requires_python_floor_stays_compatible_with_pin() -> None:
    """pyproject 的 requires-python 下界不得高于钉点（>=3.11 保持兼容 3.13）。

    收紧 requires-python 是另一个讨论（issue #483 建议第 3 条），本测试只
    防倒挂：下界若升过钉点，fresh worktree 将无法按 .python-version 建环境。
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'requires-python\s*=\s*">=(\d+\.\d+)"', text)
    assert match is not None, "pyproject.toml 缺 requires-python 下界"
    floor = tuple(int(part) for part in match.group(1).split("."))
    pin = tuple(int(part) for part in PIN.split("."))
    assert floor <= pin, f"requires-python >= {match.group(1)} 高于 .python-version 钉点 {PIN}"
