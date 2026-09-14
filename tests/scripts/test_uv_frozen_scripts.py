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
按 pyproject 正常建环境/更新 lock）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"

# uv run 后允许的下一个词（--frozen 之后）：本项目里 uv run 只用于执行
# 仓库 venv 内的工具/模块，出现其他形态（如 uv add）不属于本契约范围。
_UV_RUN_PATTERN = re.compile(r"\buv\s+run\b(?! --frozen)")


def _shell_scripts_with_uv_run() -> list[tuple[Path, int, str]]:
    offenders: list[tuple[Path, int, str]] = []
    for path in sorted(SCRIPTS_DIR.glob("*.sh")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "uv run" not in line:
                continue
            # 剔除注释行（脚本头部的纪律说明提及 uv run 本身不是调用点）。
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if _UV_RUN_PATTERN.search(line):
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
        and re.search(
            r"^\s*UV_CACHE_DIR=\S*\s*uv\s+sync\b(?! --frozen)",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    ]
    assert not offenders, (
        "不带 --frozen 的 uv sync 只允许在 scripts/install-deps.sh（bootstrap）："
        + ", ".join(path.name for path in offenders)
    )
