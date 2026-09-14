"""native-prod-up.sh 绑定地址参数化的接线与行为检查。

启动路径（依赖构建、npm/uv、健康等待）不适合单测；这里钉住
NATIVE_BACKEND_BIND / NATIVE_WORKER_BIND 的接线不变量：默认 loopback
（不设置时与历史行为一致）、uvicorn 与 worker.service 的 ``--host``
消费变量而非硬编码、健康检查按 bind 派生探测地址（绑定具体网卡时
loopback 不可达，硬编码 127.0.0.1 会误报启动失败）。风格与
test_dev_stack_local_s3.py 的静态接线检查一致；health_host 的归一
语义经提取函数体后直接执行钉死。

#486 增补：NATIVE_BACKEND/WORKER_PORT/BIND 四变量「进程环境 > 根 .env」
两级来源的三态断言（dotenv_value 原语收敛在 scripts/lib/dotenv.sh，
decide/dev_stack 两处旧实现同步收敛），以及通配 bind 的幂等边界
（#482 follow-up：通配请求命中同端口任意地址监听时视为已运行，防止
双实例连同一库的单副本退化）。整体执行的桩测试见
test_native_prod_up_exec.py（#484）。
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import tempfile
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
    """NATIVE_*_BIND 默认 127.0.0.1——未设置时保持仅本机可达的历史行为；
    #486 起读「进程环境 > 根 .env」两级来源（dotenv_value + :- 默认兜底，
    进程环境优先保留临时覆盖逃生门）。"""
    for var, name in (
        ("NATIVE_BACKEND_BIND", "BACKEND_BIND"),
        ("NATIVE_WORKER_BIND", "WORKER_BIND"),
    ):
        assert f'{name}="$(dotenv_value {var} .env)"' in NATIVE_PROD_UP
        assert f'{name}="${{{name}:-127.0.0.1}}"' in NATIVE_PROD_UP


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
    """幂等判定按「bind 地址 + 端口 + 地址族」匹配：port_listening 消费
    两个参数，族别经 lsof -i4/-i6 过滤器带入（-F n 不输出族别）——
    bindv6only=1 时 IPv6 通配不覆盖 IPv4 目标，IPv4 bind 不得被同端口
    仅 IPv6 的通配监听误判为已运行（Codex #482 P1），反之亦然。通配
    监听（*:port / [::]:port）在所属族内视为已监听（占满端口，新进程
    bind 必然 EADDRINUSE 且探测可达）。"""
    assert "listener_display" in NATIVE_PROD_UP
    assert 'port_listening "$BACKEND_BIND" "$BACKEND_PORT"' in NATIVE_PROD_UP
    assert 'port_listening "$WORKER_BIND" "$WORKER_PORT"' in NATIVE_PROD_UP
    assert '-iTCP:"$port" -i"$family"' in NATIVE_PROD_UP
    assert 'grep -Fxq -e "${display}:${port}" -e "*:${port}" -e "[::]:${port}"' in NATIVE_PROD_UP


def test_listener_family_behavior() -> None:
    """listener_family 按目标 bind 推地址族：IPv6 字面量（含括号形态）
    → 6，其余（IPv4 / 主机名）→ 4。提取函数定义后真实执行。"""
    match = re.search(r"^listener_family\(\) \{.*?^\}", NATIVE_PROD_UP, re.MULTILINE | re.DOTALL)
    assert match, "listener_family 函数定义缺失"
    code = match.group(0) + '\nfor h in "$@"; do listener_family "$h"; done\n'
    result = subprocess.run(
        [
            "bash",
            "-c",
            code,
            "listener_family",
            "127.0.0.1",
            "0.0.0.0",
            "::",
            "::1",
            "[::1]",
            "fe80::1",
            "localhost",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == ["4", "4", "6", "6", "6", "6", "4"]


def test_listener_match_behavior_dual_address() -> None:
    """双地址监听下的 port_listening 判定：同端口两个地址各自监听时，
    只命中各自的 bind，未监听的地址不误判（Codex #480 P2：127.0.0.1
    已监听时另一个地址不再被视为已运行）。真实绑定回环 + 本机网卡
    地址执行，与 test_health_host_normalization_behavior 同一提取手法。"""
    sources = []
    for name in ("listener_display", "listener_family", "port_listening"):
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


def test_listener_match_behavior_mixed_family() -> None:
    """同端口 IPv4 与 IPv6 监听并存：IPv4 目标不得命中 IPv6 监听
    （Codex #482 P1 的核心场景——无族别过滤时 *:port 会把两个族的
    通配混在一起）；down 的 listener_pids 同样不得误选。真实绑定执行
    （127.0.0.1 + ::1 各自监听；:: 通配 + IPv4 并存仅在 bindv6only=1
    时可构造，CI runner 默认双栈下 bind 冲突，见测试内注释）。"""
    up_sources = []
    for name in ("listener_display", "listener_family", "port_listening"):
        match = re.search(rf"^{name}\(\) \{{.*?^\}}", NATIVE_PROD_UP, re.MULTILINE | re.DOTALL)
        assert match, f"{name} 函数定义缺失"
        up_sources.append(match.group(0))
    up_funcs = "\n".join(up_sources)
    down = (ROOT / "scripts" / "native-prod-down.sh").read_text(encoding="utf-8")
    down_sources = []
    for name in ("listener_display", "listener_family", "listener_pids"):
        match = re.search(rf"^{name}\(\) \{{.*?^\}}", down, re.MULTILINE | re.DOTALL)
        assert match, f"down: {name} 函数定义缺失"
        down_sources.append(match.group(0))
    down_funcs = "\n".join(down_sources)

    port = _free_port()
    s4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    for sock in (s4, s6):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s4.bind(("127.0.0.1", port))
    s4.listen(1)
    # 注意不能用 :: 构造「IPv6 通配 + IPv4 并存」：Linux 默认 bindv6only=0
    # 时 :: 通配占满整个端口（含 IPv4），与 127.0.0.1 的 bind 冲突（CI
    # Linux runner 上正是这样失败的）；bindv6only=1 的场景语义用 IPv6
    # 具体地址等价覆盖——同端口两族各自监听、互不串扰。
    s6.bind(("::1", port))
    s6.listen(1)
    try:
        cases = [
            ("127.0.0.1", "yes"),  # IPv4 具体：命中自己的监听
            ("::1", "yes"),  # IPv6 具体：命中自己的监听
            ("192.0.2.99", "no"),  # IPv4 无监听：不得命中 IPv6 监听
        ]
        for bind, expected in cases:
            result = subprocess.run(
                [
                    "bash",
                    "-c",
                    up_funcs + f'\nport_listening "{bind}" "{port}" && echo yes || echo no\n',
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert result.stdout.strip() == expected, (
                f"port_listening {bind}: {result.stdout.strip()!r}"
            )
        # down：IPv4 无监听地址不得选中同端口的 IPv6 监听 pid
        result = subprocess.run(
            ["bash", "-c", down_funcs + f'\nlistener_pids "192.0.2.99" "{port}"\n'],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "", f"listener_pids 192.0.2.99: {result.stdout!r}"
    finally:
        s4.close()
        s6.close()


def test_warning_host_url_uses_bracketed_host() -> None:
    """host_url 失配警告的 URL 模板用 BACKEND_HEALTH_HOST（括号化 IPv6）
    而非裸 BACKEND_BIND——http://fd00::1:8000 无法区分地址与端口，按提示
    配置后 Worker 仍连不上（Codex #480 P2）。"""
    assert "http://$BACKEND_HEALTH_HOST:$BACKEND_PORT" in NATIVE_PROD_UP
    assert "http://$BACKEND_BIND:" not in NATIVE_PROD_UP


def test_prepends_velites_install_dir_to_service_path() -> None:
    """服务启动器把 velites 实际安装目录前置到本进程 PATH（PR #519 codex
    P1）：调用方 shell 的 PATH 可能不含 ~/.local/bin（或 VELITES_INSTALL_DIR
    指向的目录），脚本改不了父 shell 环境；不前置则后端/Worker 解析不到
    velites（或回落 data/bin 存量旧副本——#507 修的静默漂移）。目录经
    ensure-velites.sh --print-bin-dir 查询（单一事实源，启动器不得自写探测
    逻辑），且查询必须先于两个服务的 nohup 启动（后启动无效）。"""
    assert 'VELITES_BIN_DIR="$(./scripts/ensure-velites.sh --print-bin-dir)"' in NATIVE_PROD_UP
    assert 'export PATH="$VELITES_BIN_DIR:$PATH"' in NATIVE_PROD_UP
    # 单一事实源守卫：安装目录的兜底默认（~/.local/bin）只活在 ensure-velites.sh，
    # 启动器不得内嵌第二份目录探测。
    assert ".local/bin" not in NATIVE_PROD_UP
    # 前置必须先于两个服务的 nohup 启动（对已起进程前置无效）。
    export_at = NATIVE_PROD_UP.index('export PATH="$VELITES_BIN_DIR:$PATH"')
    assert export_at < NATIVE_PROD_UP.index(
        "nohup ${CAFFEINATE:+$CAFFEINATE -is} .venv/bin/python -m uvicorn"
    )


def test_prod_down_locates_by_bind_address() -> None:
    """down 脚本与 up 同一组 bind 变量、按「地址 + 端口 + 族别」定位 pid：
    up 支持同端口多地址并存后，按端口 head -1 会杀错进程；listener_pids
    精确匹配 display:port（同族通配除外），未命中即视为未运行——族别
    过滤防止误杀同端口另一族的无关监听（Codex #482 P1）。"""
    down = (ROOT / "scripts" / "native-prod-down.sh").read_text(encoding="utf-8")
    # #486：down 与 up 同一组两级来源（进程环境 > 根 .env），默认一致。
    for var, name in (
        ("NATIVE_BACKEND_BIND", "BACKEND_BIND"),
        ("NATIVE_WORKER_BIND", "WORKER_BIND"),
        ("NATIVE_BACKEND_PORT", "BACKEND_PORT"),
        ("NATIVE_WORKER_PORT", "WORKER_PORT"),
    ):
        assert f'{name}="$(dotenv_value {var} .env)"' in down
    assert 'source "$ROOT/scripts/lib/dotenv.sh"' in down
    assert "listener_pids" in down
    assert '-iTCP:"$port" -i"$family"' in down
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


# --- #486：NATIVE_* 四变量的 .env 支持（进程环境 > 根 .env > 默认） --—

NATIVE_PROD_DOWN = (ROOT / "scripts" / "native-prod-down.sh").read_text(encoding="utf-8")
DOTENV_LIB = (ROOT / "scripts" / "lib" / "dotenv.sh").read_text(encoding="utf-8")

# 四个变量在两个脚本里的接线条目（变量名、脚本内变量名、默认值）。
_FOUR_VARS = [
    ("NATIVE_BACKEND_PORT", "BACKEND_PORT", "8000"),
    ("NATIVE_WORKER_PORT", "WORKER_PORT", "8787"),
    ("NATIVE_BACKEND_BIND", "BACKEND_BIND", "127.0.0.1"),
    ("NATIVE_WORKER_BIND", "WORKER_BIND", "127.0.0.1"),
]


def test_four_vars_wire_two_level_source_in_both_scripts() -> None:
    """四个 NATIVE_* 变量在 up/down 两个脚本都改为「进程环境 > 根 .env」
    两级来源：dotenv_value（进程环境优先于 .env，与 dotenv override=False
    一致）+ ${VAR:-默认} 兜底。旧的 ${NATIVE_*:-默认} 单级形态不得残留
    （残留 = 该变量没接 .env，换 shell 会话即静默退回默认）。"""
    for text, script in (
        (NATIVE_PROD_UP, "native-prod-up.sh"),
        (NATIVE_PROD_DOWN, "native-prod-down.sh"),
    ):
        assert 'source "$ROOT/scripts/lib/dotenv.sh"' in text, script
        for var, name, default in _FOUR_VARS:
            assert f'{name}="$(dotenv_value {var} .env)"' in text, f"{script}:{var}"
            assert f'{name}="${{{name}:-{default}}}"' in text, f"{script}:{var}"
            assert f"${{{var}:-" not in text, f"{script}:{var} 单级旧形态残留"


def test_dotenv_value_priority_behavior() -> None:
    """dotenv_value 三态语义真实执行（scripts/lib/dotenv.sh 原语，up/down
    四变量的取值实现）：.env 提供值 / 进程环境覆盖 .env / 两者皆缺回落
    默认；另有文件不存在按空处理、首层引号剥离。set -euo pipefail 环境
    下跑（与消费脚本同环境，返回路径必须兼容）。"""
    with tempfile.TemporaryDirectory() as tmp:
        env_file = Path(tmp) / ".env"
        env_file.write_text(
            "NATIVE_BACKEND_PORT=9001\n"
            "NATIVE_BACKEND_BIND=0.0.0.0\n"
            'NATIVE_WORKER_BIND="192.0.2.10"\n',
            encoding="utf-8",
        )
        code = (
            DOTENV_LIB
            + "\nset -euo pipefail\n"
            + 'backend_port="$(dotenv_value NATIVE_BACKEND_PORT "$1")"\n'
            + 'backend_port="${backend_port:-8000}"\n'
            + 'backend_bind="$(dotenv_value NATIVE_BACKEND_BIND "$1")"\n'
            + 'backend_bind="${backend_bind:-127.0.0.1}"\n'
            + 'worker_bind="$(dotenv_value NATIVE_WORKER_BIND "$1")"\n'
            + 'worker_bind="${worker_bind:-127.0.0.1}"\n'
            + 'worker_port="$(dotenv_value NATIVE_WORKER_PORT "$1")"\n'
            + 'worker_port="${worker_port:-8787}"\n'
            + 'printf "%s %s %s %s\\n" "$backend_port" "$backend_bind" "$worker_bind" "$worker_port"\n'
        )
        # 测试运行者环境里的同名变量必须清掉，否则三态被进程环境污染。
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("NATIVE_")}
        # 三态 1：仅 .env 提供值。
        r_file = subprocess.run(
            ["bash", "-c", code, "dotenv", str(env_file)],
            capture_output=True,
            text=True,
            check=True,
            env=env_clean,
        )
        assert r_file.stdout.strip() == "9001 0.0.0.0 192.0.2.10 8787"

        # 三态 2：进程环境覆盖 .env（临时覆盖逃生门，override=False 语义）。
        r_env = subprocess.run(
            ["bash", "-c", code, "dotenv", str(env_file)],
            capture_output=True,
            text=True,
            check=True,
            env={**env_clean, "NATIVE_BACKEND_PORT": "9100", "NATIVE_WORKER_BIND": "::"},
        )
        assert r_env.stdout.strip() == "9100 0.0.0.0 :: 8787"

        # 三态 3：两者皆缺回落默认（.env 文件不存在按空处理）。
        r_default = subprocess.run(
            ["bash", "-c", code, "dotenv", str(Path(tmp) / "missing.env")],
            capture_output=True,
            text=True,
            check=True,
            env=env_clean,
        )
        assert r_default.stdout.strip() == "8000 127.0.0.1 127.0.0.1 8787"


def test_dotenv_value_first_and_later_file_do_not_override() -> None:
    """dotenv_value_first 严格语义（local-s3-decide.sh 的 endpoint 通道，
    收敛进 lib/dotenv.sh 后原语义不得漂移）：第一个出现该键的来源生效
    （空值也是值，不回退更低优先级来源）；键完全未出现返回 1（set -e 下
    须在条件上下文调用）。"""
    with tempfile.TemporaryDirectory() as tmp:
        first = Path(tmp) / "first.env"
        second = Path(tmp) / "second.env"
        first.write_text("KEY=\n", encoding="utf-8")
        second.write_text("KEY=from-second\n", encoding="utf-8")
        code = (
            DOTENV_LIB
            + "\nset -euo pipefail\n"
            + 'if v="$(dotenv_value_first KEY "$1" "$2")"; then printf \'[%s]\\n\' "$v"; '
            + "else printf 'absent\\n'; fi\n"
        )
        env_clean = {k: v for k, v in os.environ.items() if k != "KEY"}
        # 先出现的文件（显式空值）生效，不回退 second 文件的值。
        r = subprocess.run(
            ["bash", "-c", code, "first-semantics", str(first), str(second)],
            capture_output=True,
            text=True,
            check=True,
            env=env_clean,
        )
        assert r.stdout == "[]\n"
        # 进程环境优先于文件。
        r_env = subprocess.run(
            ["bash", "-c", code, "first-semantics", str(first), str(second)],
            capture_output=True,
            text=True,
            check=True,
            env={**env_clean, "KEY": "from-env"},
        )
        assert r_env.stdout == "[from-env]\n"
        # 键完全未出现在进程环境与任何文件 → rc 1（absent 分支）。
        missing1 = Path(tmp) / "a.env"
        missing2 = Path(tmp) / "b.env"
        r_absent = subprocess.run(
            ["bash", "-c", code, "first-absent", str(missing1), str(missing2)],
            capture_output=True,
            text=True,
            check=True,
            env=env_clean,
        )
        assert r_absent.stdout == "absent\n"


def test_local_s3_decide_reuses_shared_dotenv_lib() -> None:
    """local-s3-decide.sh 收敛为复用 lib/dotenv.sh（#486）：自身不再定义
    dotenv 解析函数（grep 的行匹配才是桩点）"""
    decide = (ROOT / "scripts" / "local-s3-decide.sh").read_text(encoding="utf-8")
    assert 'source "$SCRIPT_DIR/lib/dotenv.sh"' in decide
    assert not re.search(r"^_dotenv_value\(\)", decide, re.MULTILINE)
    assert "dotenv_value " in decide
    assert "dotenv_value_first " in decide
    # dev_stack.sh 同样收敛（read_env_value 局部副本退役）。
    dev_stack = (ROOT / "scripts" / "dev_stack.sh").read_text(encoding="utf-8")
    assert 'source "$ROOT/scripts/lib/dotenv.sh"' in dev_stack
    assert not re.search(r"^read_env_value\(\)", dev_stack, re.MULTILINE)


# --- #486 通配 bind 幂等边界（#482 follow-up） ---


def test_wildcard_bind_does_not_start_second_instance_over_existing_listener() -> None:
    """通配 bind（0.0.0.0/::）+ 同端口同族已有具体地址监听：port_listening
    判否（通配归一为 *，只匹配同族通配监听），但不得照常起进程——那会
    造出双实例连同一库（单副本退化：SSE 分裂/限速稀释/暂停互踩）。必须
    经 port_has_any_listener 兜底视为已运行、跳过启动并打醒目提示（含
    native-prod-down.sh 指引）。接线断言（行为级覆盖见整体桩测试）。"""
    assert "port_has_any_listener" in NATIVE_PROD_UP
    assert "wildcard_bind_skip" in NATIVE_PROD_UP
    # 兜底分支在两个组件的启动判定里都被消费（elif，port_listening 之后）。
    assert 'elif wildcard_bind_skip "$BACKEND_BIND" "$BACKEND_PORT" "后端"; then' in NATIVE_PROD_UP
    assert 'elif wildcard_bind_skip "$WORKER_BIND" "$WORKER_PORT" "Worker"; then' in NATIVE_PROD_UP
    # 提示文案点名风险与处置路径（用户以为换了 bind 其实没换——可由提示发现）。
    assert "不并行启动第二个实例" in NATIVE_PROD_UP
    assert "双实例连同一库会造成单副本退化" in NATIVE_PROD_UP
    assert "./scripts/native-prod-down.sh" in NATIVE_PROD_UP


def test_wildcard_bind_helpers_behavior() -> None:
    """is_wildcard_bind / port_has_any_listener 行为：通配形态识别两值；
    port_has_any_listener 对真实监听判真、无监听判假（提取函数后真实执行，
    lsof 不经桩——真实进程表）。"""
    sources = []
    for name in (
        "listener_family",
        "port_has_any_listener",
        "is_wildcard_bind",
    ):
        match = re.search(rf"^{name}\(\) \{{.*?^\}}", NATIVE_PROD_UP, re.MULTILINE | re.DOTALL)
        assert match, f"{name} 函数定义缺失"
        sources.append(match.group(0))
    funcs = "\n".join(sources)

    r2 = subprocess.run(
        [
            "bash",
            "-c",
            funcs
            + '\nfor h in "$@"; do is_wildcard_bind "$h" && echo "wild" || echo "not"; done\n',
            "is_wildcard_bind",
            "0.0.0.0",
            "::",
            "127.0.0.1",
            "::1",
            "192.0.2.1",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert r2.stdout.splitlines() == ["wild", "wild", "not", "not", "not"]

    port = _free_port()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    try:
        for family, expected in (("4", "yes"), ("6", "no")):
            r3 = subprocess.run(
                [
                    "bash",
                    "-c",
                    funcs + f'\nport_has_any_listener "{port}" "{family}" && echo yes || echo no\n',
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert r3.stdout.strip() == expected, f"family={family}: {r3.stdout.strip()!r}"
    finally:
        sock.close()


def test_specific_bind_over_wildcard_listener_is_covered_by_port_listening() -> None:
    """反向边界（请求具体 bind + 已有通配监听）：现状即跳过——port_listening
    的 grep 含 "*:${port}" 与 "[::]:${port}" 通配模式，通配监听占满所属族，
    新进程 bind 必然 EADDRINUSE。真实绑定 0.0.0.0 通配监听后验证具体
    IPv4 地址判定为已运行。"""
    sources = []
    for name in ("listener_display", "listener_family", "port_listening"):
        match = re.search(rf"^{name}\(\) \{{.*?^\}}", NATIVE_PROD_UP, re.MULTILINE | re.DOTALL)
        assert match, f"{name} 函数定义缺失"
        sources.append(match.group(0))
    funcs = "\n".join(sources)

    port = _free_port()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.listen(1)
    try:
        for bind, expected in (
            ("127.0.0.1", "yes"),  # 具体地址请求：通配监听占满端口 → 视为已运行
            ("192.0.2.99", "yes"),  # 同族任意具体地址同理
        ):
            r = subprocess.run(
                [
                    "bash",
                    "-c",
                    funcs + f'\nport_listening "{bind}" "{port}" && echo yes || echo no\n',
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            assert r.stdout.strip() == expected, f"port_listening {bind}: {r.stdout.strip()!r}"
    finally:
        sock.close()


def test_wildcard_down_reports_residual_listener_instead_of_silent_success() -> None:
    """down 的通配 bind 残留态：listener_pids 只命中「显示为通配」的进程，
    具体地址旧实例不匹配 → 未命中 pid 且同端口同族仍有任意监听时不得
    伪装成功（rc=0 跳过），改判未完全停止（rc=1）并指引（与 up 的通配
    幂等兜底配套——用户按 up 的提示来重启，down 静默 0 会让旧实例永远
    停不掉）。接线断言。"""
    assert "port_has_any_listener" in NATIVE_PROD_DOWN
    assert "is_wildcard_bind" in NATIVE_PROD_DOWN
    # 提示文案变量花括号化（裸 $VAR 紧跟多字节标点的 bash 陷阱，#484）。
    assert "绑定形态与 ${bind} 不同" in NATIVE_PROD_DOWN
    # 警告分支必须在「未命中 pid」的判定内、先于「未在运行，跳过」返回。
    warn_at = NATIVE_PROD_DOWN.index("绑定形态与 ${bind} 不同")
    skip_at = NATIVE_PROD_DOWN.index("未在运行，跳过")
    assert warn_at < skip_at
