"""Static wiring checks for the dev-stack local RustFS integration.

cmd_up 的功能路径（起 docker、建 bucket）由 tests/scripts/test_local_s3_decide.py
（决策逻辑）与 tests/scripts/test_ensure_s3_bucket.py（建 bucket）覆盖；
上半部分钉住 dev_stack.sh / init-worktree.sh / Makefile 的接线，防止后续改动
悄悄断开（与 test_local_s3_decide.py 的 prod 入口接线检查同一风格）。

下半部分是 ensure_local_rustfs 的行为级桩测试：与 test_install_deps.py 同一
手法——把 dev_stack.sh 复制进合成仓库布局，scripts/local-s3-decide.sh 与
docker/uv/lsof/curl 全部走 PATH 桩，用 STUB_* env 驱动行为并记录调用。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
DEV_STACK = (ROOT / "scripts" / "dev_stack.sh").read_text(encoding="utf-8")
INIT_WORKTREE = (ROOT / "scripts" / "init-worktree.sh").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")


def test_dev_stack_up_wires_local_s3_decision() -> None:
    """dev-up 经 local-s3-decide.sh 决策本地 RustFS（与 prod 入口同一开关）。"""
    assert "local-s3-decide.sh .env" in DEV_STACK


def test_dev_stack_starts_rustfs_via_compose_with_root_env_credentials() -> None:
    """起 rustfs 走 compose.host.yaml 单服务（profile 自动启用），凭据从根
    .env 显式 export——dev 形态没有 deploy/.env，不能让 compose 插值落空。"""
    assert "up -d rustfs" in DEV_STACK
    assert "deploy/compose.host.yaml" in DEV_STACK
    assert "read_env_value AGENT_LEGION_S3_ACCESS_KEY" in DEV_STACK
    assert "read_env_value AGENT_LEGION_S3_SECRET_KEY" in DEV_STACK


def test_dev_stack_ensures_bucket_after_start() -> None:
    assert "ensure-s3-bucket.py" in DEV_STACK


def test_dev_stack_status_shows_rustfs() -> None:
    """cmd_status 显示 rustfs 容器状态（docker 缺失时跳过）。"""
    status = DEV_STACK.split("cmd_status() {", 1)[1]
    assert "rustfs" in status
    assert "command -v docker" in status


def test_init_worktree_delegates_bucket_creation() -> None:
    """init-worktree.sh 与 dev_stack.sh 共用 ensure-s3-bucket.py，
    不再内嵌 boto3 heredoc（两份实现必然漂移）。"""
    assert "ensure-s3-bucket.py" in INIT_WORKTREE
    assert "import boto3" not in INIT_WORKTREE


def test_makefile_wires_install_target() -> None:
    assert "install:" in MAKEFILE
    assert "scripts/install-deps.sh" in MAKEFILE


# --- ensure_local_rustfs 行为级桩测试（合成仓库 + PATH 桩） ---

DEV_STACK_SCRIPT = ROOT / "scripts" / "dev_stack.sh"

# 决策脚本桩：STUB_DECIDE_RC 非 0 时按该码退出（stdout 无决策词），
# 否则输出 STUB_DECISION（默认 start）。
_DECIDE_STUB = """#!/usr/bin/env bash
echo "本地 RustFS: stub 决策原因" >&2
if [[ "${STUB_DECIDE_RC:-0}" != "0" ]]; then exit "${STUB_DECIDE_RC}"; fi
echo "${STUB_DECISION:-start}"
"""

# docker 桩：记录全部调用；ps 按 STUB_RUSTFS_RUNNING 报告 rustfs，
# up -d 按 STUB_UP_RC 失败/成功。
_DOCKER_STUB = """#!/usr/bin/env bash
echo "docker $*" >> "${STUB_LOG}"
if [[ "$*" == *"ps --status running --services"* ]]; then
  if [[ "${STUB_RUSTFS_RUNNING:-}" == "1" ]]; then echo "rustfs"; fi
  exit 0
fi
if [[ "$*" == *"up -d rustfs"* ]]; then
  exit "${STUB_UP_RC:-0}"
fi
exit 0
"""

_UV_STUB = """#!/usr/bin/env bash
echo "uv $*" >> "${STUB_LOG}"
exit 0
"""

# 端口全部视为已监听（组件跳过启动）、HTTP 全部视为就绪（curl 成功），
# 让 cmd_up 在 rustfs 段之后直接走到 print_summary。
_EXIT_OK_STUB = """#!/usr/bin/env bash
exit 0
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path, *, with_docker: bool = True) -> tuple[Path, Path]:
    """合成仓库布局：真实 dev_stack.sh + 桩决策脚本 + PATH 桩。"""
    main = tmp_path / "main"
    (main / "scripts").mkdir(parents=True)
    (main / "deploy").mkdir()
    (main / "frontend" / "node_modules").mkdir(parents=True)
    shutil.copy(DEV_STACK_SCRIPT, main / "scripts" / DEV_STACK_SCRIPT.name)
    _write_stub(main / "scripts" / "local-s3-decide.sh", _DECIDE_STUB)
    (main / "deploy" / "compose.host.yaml").write_text("name: agent-legion\n")
    (main / ".env").write_text("AGENT_LEGION_S3_ACCESS_KEY=ak\nAGENT_LEGION_S3_SECRET_KEY=sk\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if with_docker:
        _write_stub(bin_dir / "docker", _DOCKER_STUB)
    _write_stub(bin_dir / "uv", _UV_STUB)
    for tool in ("lsof", "curl"):
        _write_stub(bin_dir / tool, _EXIT_OK_STUB)
    return main, bin_dir


def _run_up(
    main: Path,
    bin_dir: Path,
    stub_log: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    # 清掉测试运行者环境里的同名变量，避免污染合成仓库的决策/端口。
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AGENT_LEGION_", "DEV_", "AGENT_WORKER_"))
    }
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    env["STUB_LOG"] = str(stub_log)
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(main / "scripts" / DEV_STACK_SCRIPT.name), "up"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_invalid_toggle_rc2_degrades_to_warning(tmp_path: Path) -> None:
    """rc=2（开关值非法）：dev 降级为告警不阻断，且不触碰 docker。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run_up(main, bin_dir, stub_log, {"STUB_DECIDE_RC": "2"})

    assert result.returncode == 0, result.stderr
    assert "开关值非法" in result.stderr
    assert not stub_log.exists() or "docker" not in stub_log.read_text()


def test_missing_credentials_rc3_warns_and_skips(tmp_path: Path) -> None:
    """rc=3（已表达本地存储意图但凭据未配齐）：告警不启动，不触碰 docker。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run_up(main, bin_dir, stub_log, {"STUB_DECIDE_RC": "3"})

    assert result.returncode == 0, result.stderr
    assert "跳过本地 RustFS 启动" in result.stderr
    assert not stub_log.exists() or "docker" not in stub_log.read_text()


def test_no_docker_degrades_gracefully(tmp_path: Path) -> None:
    """无 docker：提示降级 503，不阻断 dev-up。"""
    main, bin_dir = _setup(tmp_path, with_docker=False)
    stub_log = tmp_path / "stub.log"

    result = _run_up(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert "未检测到 docker" in result.stderr


def test_running_rustfs_skips_recreate(tmp_path: Path) -> None:
    """rustfs 容器已在运行（可能与 prod/其他 worktree 共享）：跳过 up -d
    防 recreate 打断共享方（PR #232），仍照常确保 bucket。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run_up(main, bin_dir, stub_log, {"STUB_RUSTFS_RUNNING": "1"})

    assert result.returncode == 0, result.stderr
    assert "跳过 recreate" in result.stdout
    log = stub_log.read_text()
    assert "ps --status running --services" in log
    assert "up -d rustfs" not in log
    assert "ensure-s3-bucket.py" in log  # bucket 确认不受影响


def test_stopped_rustfs_is_started_via_compose(tmp_path: Path) -> None:
    """rustfs 未运行：经 compose up -d 启动并确保 bucket（对照组，钉住
    skip-recreate 只作用于「已在运行」分支）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run_up(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    log = stub_log.read_text()
    assert "up -d rustfs" in log
    assert "ensure-s3-bucket.py" in log
    assert "已就绪" in result.stdout
