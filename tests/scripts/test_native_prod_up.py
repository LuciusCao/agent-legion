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
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
NATIVE_PROD_UP = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")


def _first_lan_ipv4() -> str:
    output = subprocess.run(["ifconfig"], capture_output=True, text=True, check=True).stdout
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("inet ") and not line.startswith("inet 127."):
            return line.split()[1]
    pytest.skip("no non-loopback IPv4 address available")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _bind_listeners(addresses: list[str], port: int) -> list[Any]:
    sockets = []
    for addr in addresses:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((addr, port))
        sock.listen(1)
        sockets.append(sock)
    return sockets


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
    assert "0.0.0.0) host=127.0.0.1 ;;" in NATIVE_PROD_UP
    assert '::) host="[::1]" ;;' in NATIVE_PROD_UP
    assert "BACKEND_HEALTH_HOST=" in NATIVE_PROD_UP
    assert "WORKER_HEALTH_HOST=" in NATIVE_PROD_UP
    assert (
        "curl -sS -m 2 --noproxy '*' --fail -o /dev/null \"http://$BACKEND_HEALTH_HOST:$BACKEND_PORT/api/health\""
        in NATIVE_PROD_UP
    )
    assert (
        "curl -sS -m 2 --noproxy '*' --fail -o /dev/null \"http://$WORKER_HEALTH_HOST:$WORKER_PORT/api/health\""
        in NATIVE_PROD_UP
    )
    assert "http://127.0.0.1:$BACKEND_PORT" not in NATIVE_PROD_UP
    assert "http://127.0.0.1:$WORKER_PORT" not in NATIVE_PROD_UP


def test_health_probe_bypasses_proxy_and_requires_http_success() -> None:
    """本机健康探测必须绕过环境代理（http_proxy 对局域网地址同样生效，
    代理不可达会误报失败）且以 HTTP 2xx 为就绪判据（--fail：代理返 403
    等错误码不得计为就绪）。"""
    assert "--noproxy '*'" in NATIVE_PROD_UP
    assert "--fail" in NATIVE_PROD_UP


def test_health_host_normalization_behavior() -> None:
    """health_host 归一语义：0.0.0.0（IPv4 全接口）归一 IPv4 loopback，
    ::（IPv6 全接口，bindv6only=1 时不收 IPv4）归一 [::1]，具体地址
    原样，IPv6 字面量补 URL 方括号且已带方括号时幂等。提取函数定义后
    真实执行。"""
    match = re.search(r"^health_host\(\) \{.*?^\}", NATIVE_PROD_UP, re.MULTILINE | re.DOTALL)
    assert match, "health_host 函数定义缺失"
    code = match.group(0) + '\nfor h in "$@"; do health_host "$h"; done\n'
    result = subprocess.run(
        [
            "bash",
            "-c",
            code,
            "health_host",
            "127.0.0.1",
            "0.0.0.0",
            "::",
            "192.0.2.1",
            "::1",
            "[fe80::1]",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "127.0.0.1",
        "127.0.0.1",
        "[::1]",
        "192.0.2.1",
        "[::1]",
        "[fe80::1]",
    ]


def test_idempotency_matches_bind_address() -> None:
    """幂等判定按「bind 地址 + 端口」匹配：port_listening 消费两个参数，
    通配监听（*:port / [::]:port）也算已监听（占满端口，新进程 bind 必然
    EADDRINUSE 且探测可达）——只看端口会把其它地址的监听误认为本服务
    而跳过启动，随后按 bind 探测必然失败（同端口不同地址可并存）。"""
    assert "listener_display" in NATIVE_PROD_UP
    assert 'port_listening "$BACKEND_BIND" "$BACKEND_PORT"' in NATIVE_PROD_UP
    assert 'port_listening "$WORKER_BIND" "$WORKER_PORT"' in NATIVE_PROD_UP
    assert 'grep -Fxq -e "${display}:${port}" -e "*:${port}" -e "[::]:${port}"' in NATIVE_PROD_UP


def test_listener_match_behavior_dual_address() -> None:
    """双地址监听下的 port_listening 判定：同端口两个地址各自监听时，
    只命中各自的 bind，未监听的地址不误判（Codex #480 P2：127.0.0.1
    已监听时另一个地址不再被视为已运行）。真实绑定回环 + 本机网卡
    地址执行，与 test_health_host_normalization_behavior 同一提取手法。"""
    sources = []
    for name in ("listener_display", "port_listening"):
        match = re.search(rf"^{name}\(\) \{{.*?^\}}", NATIVE_PROD_UP, re.MULTILINE | re.DOTALL)
        assert match, f"{name} 函数定义缺失"
        sources.append(match.group(0))
    funcs = "\n".join(sources)

    lan_ip = _first_lan_ipv4()
    port = _free_port()
    sockets = _bind_listeners(["127.0.0.1", lan_ip], port)
    try:
        checks = [("127.0.0.1", "yes"), (lan_ip, "yes"), ("127.0.0.3", "no"), ("192.0.2.99", "no")]
        for bind, expected in checks:
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    funcs + f'\nport_listening "{bind}" "{port}" && echo yes || echo no\n',
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert result.stdout.strip() == expected, (
                f"port_listening {bind}: {result.stdout.strip()!r}"
            )
    finally:
        for sock in sockets:
            sock.close()


def test_warning_host_url_uses_bracketed_host() -> None:
    """host_url 失配警告的 URL 模板用 BACKEND_HEALTH_HOST（括号化 IPv6）
    而非裸 BACKEND_BIND——http://fd00::1:8000 无法区分地址与端口，按提示
    配置后 Worker 仍连不上（Codex #480 P2）。"""
    assert "http://$BACKEND_HEALTH_HOST:$BACKEND_PORT" in NATIVE_PROD_UP
    assert "http://$BACKEND_BIND:" not in NATIVE_PROD_UP


def test_prod_down_locates_by_bind_address() -> None:
    """down 脚本与 up 同一组 bind 变量、按「地址 + 端口」定位 pid：up 支持
    同端口多地址并存后，按端口 head -1 会杀错进程；listener_pids 精确
    匹配 display:port（通配除外），未命中即视为未运行。"""
    down = (ROOT / "scripts" / "native-prod-down.sh").read_text(encoding="utf-8")
    assert 'BACKEND_BIND="${NATIVE_BACKEND_BIND:-127.0.0.1}"' in down
    assert 'WORKER_BIND="${NATIVE_WORKER_BIND:-127.0.0.1}"' in down
    assert 'listener_pids "$bind" "$port"' in down
    assert 'stop_port "$WORKER_BIND" "$WORKER_PORT" "Worker" 35' in down
    assert 'stop_port "$BACKEND_BIND" "$BACKEND_PORT" "后端" 15' in down
    assert down.count("lsof -nP -tiTCP") == 0  # 旧式仅按端口取 pid 的调用不得残留


def test_local_worker_loopback_mismatch_warns() -> None:
    """bind 具体网卡时的本地接入提醒接线：本地 Worker 状态副本的 host_url
    默认 loopback，bind 非 loopback 后它会静默退避重试注册（不崩溃、
    不易察觉），警告是唯一的操作面提示；脚本只提示不代改（#323）。"""
    assert "binds_specific_interface" in NATIVE_PROD_UP
    assert "host_url:[[:space:]]*https?://(127\\.|localhost)" in NATIVE_PROD_UP
    assert "本地 Worker 状态副本的 host_url 仍指向 loopback" in NATIVE_PROD_UP
    assert "127.0.0.1 不再监听" in NATIVE_PROD_UP


def test_binds_specific_interface_behavior() -> None:
    """binds_specific_interface 语义：loopback 各形态与全接口通配都不算
    「具体网卡」，只有具体地址触发提醒。提取函数定义后真实执行。"""
    sources = []
    for name in ("is_loopback", "binds_specific_interface"):
        match = re.search(rf"^{name}\(\) \{{.*?^\}}", NATIVE_PROD_UP, re.MULTILINE | re.DOTALL)
        assert match, f"{name} 函数定义缺失"
        sources.append(match.group(0))
    code = (
        "\n".join(sources)
        + '\nfor h in "$@"; do binds_specific_interface "$h" && echo "$h specific" || echo "$h not"; done\n'
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            code,
            "predicates",
            "127.0.0.1",
            "::1",
            "localhost",
            "0.0.0.0",
            "::",
            "192.0.2.1",
            "fe80::1",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "127.0.0.1 not",
        "::1 not",
        "localhost not",
        "0.0.0.0 not",
        ":: not",
        "192.0.2.1 specific",
        "fe80::1 specific",
    ]
