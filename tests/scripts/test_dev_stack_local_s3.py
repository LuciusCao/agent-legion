"""Static wiring checks for the dev-stack local RustFS integration.

cmd_up 的功能路径（起 docker、建 bucket）由 tests/scripts/test_local_s3_decide.py
（决策逻辑）与 tests/scripts/test_ensure_s3_bucket.py（建 bucket）覆盖；
这里钉住 dev_stack.sh / init-worktree.sh / Makefile 的接线，防止后续改动
悄悄断开（与 test_local_s3_decide.py 的 prod 入口接线检查同一风格）。
"""

from __future__ import annotations

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
