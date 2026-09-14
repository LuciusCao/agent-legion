"""Contract tests for the --frozen discipline of scripts/' uv invocations.

issue #526：0.8.0 开发期间多个 worktree 的 uv.lock 被同样的镜像 URL 重写
污染（pypi.org → 镜像源，hash 全不变）。根因是 scripts/ 里不带 --frozen 的
``uv run``——开发者 shell 会话带 ``UV_DEFAULT_INDEX``/``UV_INDEX_URL`` 镜像
环境时（本机经慢网用镜像装依赖是常态），这些调用触发 re-lock 把镜像 URL
写进 uv.lock；污染若漏进 PR 会直接打不进 CI（镜像 URL 在 GitHub Actions
环境不可达/不该存在）。

契约：``scripts/*.sh`` 里每个 ``uv run`` 都必须带 ``--frozen``（依赖从冻结
lock 解析安装——fresh worktree 无 .venv 时也按 lock 建环境；frozen 调用绝不
写 lock）。唯一豁免：``uv sync`` 形态的 bootstrap（install-deps.sh 第 2 步
按 pyproject 正常建环境/更新 lock）。调用识别用整词正则（#483 审查加固）：
``$UV run``、``uv "run"``、``uv `run` `` 等变体同样算 ``uv run`` 调用，裸
``uv sync``（无论是否带 ``UV_CACHE_DIR=`` 前缀）同样算 ``uv sync``。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"

# 「uv run」调用的整词识别（#483 审查加固）：$UV/$uv 变量形态、大小写
# 混写、run 被引号/反引号包裹（uv "run"、uv `run`）都算提及——纯子串
# 预过滤（"uv run" not in line）漏得掉这些变体，行级过滤必须用同一正则。
# 本项目里 uv run 只用于执行仓库 venv 内的工具/模块，其他形态（如 uv add）
# 不属于本契约范围。
_UV_RUN_MENTION_SRC = r"\$?\b[uU][vV]\s+[`\"']*\brun\b"
_UV_RUN_MENTION = re.compile(_UV_RUN_MENTION_SRC)
# 违规 = 提及 uv run 且该调用不带 --frozen。run 的收尾引号/反引号用占有型
# 量词吞掉（要求 Python >= 3.11 的 re，与 requires-python 一致）：防回溯把
# `uv "run" --frozen`（合法 frozen 调用）误判成违规。
_UV_RUN_OFFENDER = re.compile(_UV_RUN_MENTION_SRC + r"[`\"']*+(?! --frozen)")

# 非 frozen 的 uv sync（按 pyproject 建环境/更新 lock 的 bootstrap）只允许
# 在 install-deps.sh；UV_CACHE_DIR= 前缀可选——裸 uv sync（如未来的
# uv sync --all-extras）不得借「无前缀」绕过（#483 审查加固）。
_UV_SYNC_OFFENDER = re.compile(r"^\s*(UV_CACHE_DIR=\S+\s+)?uv\s+sync\b(?! --frozen)", re.MULTILINE)


def _shell_scripts_with_uv_run() -> list[tuple[Path, int, str]]:
    offenders: list[tuple[Path, int, str]] = []
    for path in sorted(SCRIPTS_DIR.glob("*.sh")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _UV_RUN_MENTION.search(line):
                continue
            # 剔除注释行（脚本头部的纪律说明提及 uv run 本身不是调用点）。
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if _UV_RUN_OFFENDER.search(line):
                offenders.append((path, lineno, line.strip()))
    return offenders


def test_every_uv_run_in_scripts_is_frozen() -> None:
    """scripts/*.sh 的每个 uv run 调用必须带 --frozen（issue #526）。

    漂移时 fail-fast 列出文件:行号，而不是等下一次镜像污染事故取证。
    """
    offenders = _shell_scripts_with_uv_run()
    assert not offenders, (
        "scripts/ 里存在不带 --frozen 的 uv run 调用（issue #526："
        "镜像 index 环境下会触发 re-lock 重写 uv.lock）：\n"
        + "\n".join(f"  {path.name}:{lineno}: {line}" for path, lineno, line in offenders)
    )


def test_uv_sync_bootstrap_stays_in_install_deps() -> None:
    """非 frozen 的 uv sync（按 pyproject 建环境/更新 lock 的 bootstrap）只
    允许出现在 install-deps.sh——其他脚本一律走 frozen 形态（uv run --frozen
    或 uv sync --frozen），没有第二个会写 lock 的入口。"""
    offenders = [
        path
        for path in sorted(SCRIPTS_DIR.glob("*.sh"))
        if path.name != "install-deps.sh"
        and _UV_SYNC_OFFENDER.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "不带 --frozen 的 uv sync 只允许在 scripts/install-deps.sh（bootstrap）："
        + ", ".join(path.name for path in offenders)
    )


@pytest.mark.parametrize(
    ("line", "is_offender"),
    [
        # 不带 --frozen 的调用：直接形态与绕过变体都必须被抓（#483 审查：
        # 旧版子串预过滤 + 纯小写正则对以下形态全部失明）。
        ("uv run python -c 'x'", True),
        ("UV_CACHE_DIR=.uv-cache uv run pytest -q", True),
        ("$UV run python x.py", True),
        ("$uv run python x.py", True),
        ('uv "run" python x.py', True),
        ("uv 'run' python x.py", True),
        ("uv `run` python x.py", True),
        ("echo `uv run python x.py`", True),
        # 合法 frozen 调用不抓（含引号/变量变体——占有型量词防回溯误判）。
        ("uv run --frozen pytest -q", False),
        ("UV_CACHE_DIR=.uv-cache uv run --frozen ruff check .", False),
        ("$UV run --frozen python x.py", False),
        ('uv "run" --frozen python x.py', False),
        # 相邻但不是 uv run 调用的行不抓。
        ("uv sync --all-extras", False),
        ("UV_CACHE_DIR=.uv-cache uv add requests", False),
        ("uv runner --frozen x", False),
        ("echo 'some uv running text'", False),
    ],
)
def test_uv_run_offender_catches_bypass_variants(line: str, is_offender: bool) -> None:
    """整词正则的回归钉：预过滤与违规判定共用同一 mention 源。"""
    assert bool(_UV_RUN_OFFENDER.search(line)) is is_offender
    # 违规行必须先过行级预过滤（否则端到端仍会被预过滤吃掉）。
    if is_offender:
        assert _UV_RUN_MENTION.search(line)


@pytest.mark.parametrize(
    ("line", "is_offender"),
    [
        # 裸 uv sync（无 UV_CACHE_DIR= 前缀）不再绕过（#483 审查）。
        ("uv sync", True),
        ("uv sync --all-extras", True),
        ("UV_CACHE_DIR=.uv-cache uv sync", True),
        # frozen 形态与非 sync 调用不抓。
        ("uv sync --frozen", False),
        ("UV_CACHE_DIR=.uv-cache uv sync --frozen", False),
        ("uv run --frozen pytest -q", False),
        ("uv add requests", False),
    ],
)
def test_uv_sync_offender_catches_bare_invocations(line: str, is_offender: bool) -> None:
    """uv sync 契约的前缀可选化回归钉。"""
    assert bool(_UV_SYNC_OFFENDER.search(line)) is is_offender
