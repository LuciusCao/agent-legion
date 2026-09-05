"""native-prod-up.sh 绑定地址参数化的接线与行为检查。

启动路径（依赖构建、npm/uv、健康等待）不适合单测；这里钉住
NATIVE_BACKEND_BIND / NATIVE_WORKER_BIND 的接线不变量：默认 loopback
（不设置时与历史行为一致）、uvicorn 与 worker.service 的 ``--host``
消费变量而非硬编码、健康检查按 bind 派生探测地址（绑定具体网卡时
loopback 不可达，硬编码 127.0.0.1 会误报启动失败）。风格与
test_dev_stack_local_s3.py 的静态接线检查一致；health_host 的归一
语义经提取函数体后直接执行钉死。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
NATIVE_PROD_UP = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")


def test_bind_env_vars_default_to_loopback() -> None:
    """NATIVE_*_BIND 默认 127.0.0.1——未设置时保持仅本机可达的历史行为。"""
    assert 'BACKEND_BIND="${NATIVE_BACKEND_BIND:-127.0.0.1}"' in NATIVE_PROD_UP
    assert 'WORKER_BIND="${NATIVE_WORKER_BIND:-127.0.0.1}"' in NATIVE_PROD_UP


def test_processes_consume_bind_variables() -> None:
    """uvicorn 与 worker.service 的 --host 消费 bind 变量，不得回退硬编码。"""
    assert '--host "$BACKEND_BIND"' in NATIVE_PROD_UP
    assert '--host "$WORKER_BIND"' in NATIVE_PROD_UP
    assert "--host 127.0.0.1" not in NATIVE_PROD_UP


def test_health_checks_derive_probe_host_from_bind() -> None:
    """健康检查经 health_host 派生探测地址；curl 不得再硬编码 127.0.0.1
    （绑定具体网卡时 loopback 无监听，会让就绪等待误判 5 分钟超时）。"""
    assert "0.0.0.0 | ::) host=127.0.0.1 ;;" in NATIVE_PROD_UP
    assert "BACKEND_HEALTH_HOST=" in NATIVE_PROD_UP
    assert "WORKER_HEALTH_HOST=" in NATIVE_PROD_UP
    assert 'curl -sS -m 2 "http://$BACKEND_HEALTH_HOST:$BACKEND_PORT/api/health"' in NATIVE_PROD_UP
    assert 'curl -sS -m 2 "http://$WORKER_HEALTH_HOST:$WORKER_PORT/api/health"' in NATIVE_PROD_UP
    assert "http://127.0.0.1:$BACKEND_PORT" not in NATIVE_PROD_UP
    assert "http://127.0.0.1:$WORKER_PORT" not in NATIVE_PROD_UP


def test_health_host_normalization_behavior() -> None:
    """health_host 归一语义：全接口监听归一 loopback，具体地址原样，
    IPv6 字面量补 URL 方括号。提取函数定义后真实执行。"""
    match = re.search(r"^health_host\(\) \{.*?^\}", NATIVE_PROD_UP, re.MULTILINE | re.DOTALL)
    assert match, "health_host 函数定义缺失"
    code = match.group(0) + '\nfor h in "$@"; do health_host "$h"; done\n'
    result = subprocess.run(
        ["bash", "-c", code, "health_host", "127.0.0.1", "0.0.0.0", "::", "192.0.2.1", "::1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "127.0.0.1",
        "127.0.0.1",
        "127.0.0.1",
        "192.0.2.1",
        "[::1]",
    ]


def test_prod_down_stays_bind_agnostic() -> None:
    """native-prod-down.sh 按端口定位进程，与 bind 无关——bind 覆盖不需要
    停机侧配套改动（若未来停机按地址定位，此断言会提醒同步）。"""
    down = (ROOT / "scripts" / "native-prod-down.sh").read_text(encoding="utf-8")
    assert "stop_port" in down
    assert "NATIVE_BACKEND_BIND" not in down
