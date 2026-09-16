"""prod-restart.sh 原子重启入口的语法与接线检查（issue #629）。

真实 down/up 依赖生产进程与端口，不适合单测；这里钉住与
test_native_prod_up.py 同类的静态接线不变量：脚本存在且可执行、语法
合法、down 与 up 的调用顺序（down 先于 up，重试前清残留）、失败路径
打印人工恢复指引并以非零退出、重试次数经环境变量可覆盖。完整 down+
up+healthcheck 编排是部署机上的真实动作，属于人工验证范畴。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "prod-restart.sh"


def test_script_is_executable_and_syntax_valid() -> None:
    """脚本必须可执行（./scripts/… 直接调用形态；tracked exec-bit 门禁
    #623 在 CI 侧盯 git index 模式，这里盯工作区）。"""
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & 0o111, "prod-restart.sh 必须带执行位"
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"bash -n 语法错误: {result.stderr}"


def test_down_runs_before_up_and_clears_residue_between_retries() -> None:
    """顺序不变量：down 先于 up；up 失败的重试轮之间再跑一次 down 清
    残留（半启动进程占端口会让 up 的幂等判定误判「已在运行」）。只匹配
    实际调用行（行首 `./scripts/…`，含 `if !` 前缀），头部注释里的提及
    不算调用。"""
    text = SCRIPT.read_text(encoding="utf-8")
    call = re.compile(r"^\s*(?:if\s*)?!?\s*\./scripts/native-prod-(down|up)\.sh", re.MULTILINE)
    calls = [(m.start(), m.group(1)) for m in call.finditer(text)]
    assert [kind for _, kind in calls] == ["down", "up", "down"], (
        f"调用序列应为 down(主)→up(主)→down(重试清残留)，实际: {[kind for _, kind in calls]}"
    )


def test_retry_count_and_wait_are_env_overridable() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'UP_RETRIES="${PROD_RESTART_UP_RETRIES:-3}"' in text
    assert 'UP_RETRY_WAIT_SECONDS="${PROD_RESTART_UP_RETRY_WAIT_SECONDS:-10}"' in text


def test_failure_path_prints_manual_recovery_guidance() -> None:
    """全部重试耗尽后：不得静默成功（非零退出），打印诊断步骤与手动
    恢复指引（日志路径、端口检查、native-prod-up.sh）。"""
    text = SCRIPT.read_text(encoding="utf-8")
    for needle in (
        "data/logs/prod-backend.log",
        "data/logs/prod-worker.log",
        "lsof -nP -iTCP:8000 -iTCP:8787 -sTCP:LISTEN",
        "./scripts/native-prod-up.sh",
    ):
        assert needle in text, f"恢复指引缺少 {needle}"
    # 以 up 的实际失败退出码退出（0 时兜底 1）。
    assert re.search(r'exit "\$\(\( up_rc == 0 \? 1 : up_rc \)\)"', text)


def test_up_rc_captured_from_up_not_from_if_statement() -> None:
    """up 的退出码必须在 else 分支里立刻捕获（review #629 修复）：
    空体 `if` 语句自身恒返回 0，把 `up_rc=$?` 放在语句之后会掩盖真实
    退出码——重试警告报「退出码 0」、最终 exit 丢失 up 的非零码。"""
    text = SCRIPT.read_text(encoding="utf-8")
    assert re.search(
        r"if \./scripts/native-prod-up\.sh; then\n"
        r"\s*echo \"== 重启完成：生产环境已就绪 ==\"\n"
        r"\s*exit 0\n"
        r"\s*else\n"
        r"\s*up_rc=\$\?",
        text,
    ), "up_rc 必须在 else 分支捕获（`if` 语句会掩盖 $?）"


def test_blocked_make_target_includes_prod_restart() -> None:
    """接线一致性：prod-restart 是 make target（Makefile），而 terminal
    禁止清单把 `make prod-restart` 与 `./scripts/prod-restart.sh` 一并
    拒绝——脚本本身是原子单元，但在 agent 会话里跑它依旧会在「down 已
    完成、up 未完成」的窗口被打断（脚本无法对抗 terminal/kill 的组杀，
    见 terminals.py 的 SIGKILL 路径），所以本期它仍是人工专用入口。"""
    from server.app.studio_chat.terminal_guard import (
        BLOCKED_MAKE_TARGETS,
        BLOCKED_SCRIPTS,
    )

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "prod-restart:" in makefile, "Makefile 必须提供 prod-restart target"
    assert "prod-restart" in BLOCKED_MAKE_TARGETS
    assert "prod-restart.sh" in BLOCKED_SCRIPTS
