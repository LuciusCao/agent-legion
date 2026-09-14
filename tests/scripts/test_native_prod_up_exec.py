"""native-prod-up.sh 整体执行的行为级桩测试（issue #484）。

与 test_native_prod_up.py 的两类旧手法互补：旧手法只做静态接线断言或
提取单个函数真跑，从不执行整个脚本——listener_display 被误删、调用点
残留那类「函数悬空引用」断裂（#480/#482 期间真实发生）两者都抓不到，
``bash -n`` 也只查语法不解析定义。这里把脚本放进合成仓库布局（frontend/
目录、.env、data/、deploy/uvicorn-log-config.json 等）整体真跑：
lsof/npm/uv/docker/curl/caffeinate/ensure-velites 全走 PATH 桩
（STUB_* env 驱动、调用序列记入 STUB_LOG），set -euo pipefail 与非零
退出的交互、幂等/健康检查/警告块/通配兜底的组合行为都在真实脚本级执行
下覆盖。手法与 test_install_deps.py / test_dev_stack_local_s3.py 同源。

桩矩阵核心是 lsof 桩（port_listening / port_has_any_listener /
listener_pids 的共同底座）：STUB_LISTENERS 形如 "127.0.0.1:8000,*:8787"
（lsof -F n 的 display:port 全集，逗号分隔；-F pn 时每行配 STUB_PID），
按 -iTCP:port 参数过滤——「port_listening 判否但同端口仍有任意地址监听」
的通配兜底场景由「display 与请求 bind 形态不同、端口相同」的条目自然
构造。sleep 桩为 no-op：健康检查失败路径 150 次循环瞬间跑完，断言超时
退出码 1 无需真实等待 5 分钟。

首跑即抓到一个真实断裂（正是 issue #484 描述的「测试天花板是作者的
认知边界」）：提示文案里「裸 $VAR 紧跟多字节标点」（$WORKER_BIND，）
——bash 把标点的首字节误并入变量名，set -u 下对已赋值变量报
「unbound variable」并退出 1，bind 非 loopback 的启动路径整体不可用
（bash 3.2 与 5 皆然）。静态接线与函数提取两类旧手法对此无感：字符串
在脚本里、函数也无须执行到该行。修复为提示文案变量一律花括号。
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
UP_SCRIPT = ROOT / "scripts" / "native-prod-up.sh"
DOWN_SCRIPT = ROOT / "scripts" / "native-prod-down.sh"

# --- PATH 桩 ---

# lsof 桩：解析 -F（输出字段，兼容 `-F n` 空格分离与 `-F pn` 连写两种
# 形态——up 用分离式、down 用连写式）与 -iTCP:port（端口过滤）参数，按
# STUB_LISTENERS（display:port 全集，逗号分隔）输出监听记录；-F 含 p 时
# 每个 n 记录配一个 p 记录（pid 取 STUB_PID，默认 4242）。无匹配输出空。
_LSOF_STUB = """#!/usr/bin/env bash
fields=""
port=""
expect_fields=0
for arg in "$@"; do
  if [[ "$expect_fields" == "1" ]]; then fields="$arg"; expect_fields=0; continue; fi
  case "$arg" in
    -F) expect_fields=1 ;;
    -F*) fields="${arg#-F}" ;;
    -iTCP:*) port="${arg#-iTCP:}" ;;
  esac
done
IFS=',' read -r -a entries <<< "${STUB_LISTENERS:-}"
for entry in "${entries[@]}"; do
  [[ -n "$entry" ]] || continue
  entry_port="${entry##*:}"
  [[ -z "$port" || "$entry_port" == "$port" ]] || continue
  if [[ "$fields" == *p* ]]; then printf 'p%s\\n' "${STUB_PID:-4242}"; fi
  if [[ "$fields" == *n* ]]; then printf 'n%s\\n' "$entry"; fi
done
exit 0
"""

# npm 桩：ci/build 记录后成功（build 顺手创建 dist 目录，脚本不消费内容）。
_NPM_STUB = """#!/usr/bin/env bash
echo "npm $*" >> "${STUB_LOG}"
if [[ "$1" == "run" && "$2" == "build" ]]; then
  mkdir -p "${STUB_FRONTEND_DIST:-frontend/dist}"
fi
exit 0
"""

# uv 桩：sync 记录后成功（真实 uv sync 会建 .venv；合成布局直接预置
# .venv/bin/python 为记录桩，nohup 启动的服务进程参数全落在日志里）。
_UV_STUB = """#!/usr/bin/env bash
echo "uv $*" >> "${STUB_LOG}"
exit 0
"""

_PYTHON_STUB = """#!/usr/bin/env bash
echo "python $*" >> "${STUB_LOG}"
exit 0
"""

# ensure-velites 桩：记录调用；--print-bin-dir 输出桩目录（脚本会把它
# 前置进 PATH，对后续桩查找无影响）。
_ENSURE_VELITES_STUB = """#!/usr/bin/env bash
echo "ensure-velites $*" >> "${STUB_LOG}"
if [[ "$1" == "--print-bin-dir" ]]; then echo "/stub-velites-bin"; fi
exit 0
"""

# 决策脚本桩：复刻 test_dev_stack_local_s3.py 的 _DECIDE_STUB 语义
# （--service-name 分派 / STUB_DECIDE_RC 退出码 / 决策词 stdout）。
_DECIDE_STUB = """#!/usr/bin/env bash
if [[ "$1" == "--service-name" ]]; then
  echo "${STUB_BACKEND:-seaweedfs}"
  exit 0
fi
echo "本地对象存储: stub 决策原因" >&2
if [[ "${STUB_DECIDE_RC:-0}" != "0" ]]; then exit "${STUB_DECIDE_RC}"; fi
echo "${STUB_DECISION:-start}"
"""

_DOCKER_STUB = """#!/usr/bin/env bash
echo "docker $*" >> "${STUB_LOG}"
exit 0
"""

# curl 桩：按 STUB_CURL_OK（yes/no/backend/worker）控制健康端点成败。
# URL 是最后一个参数（${!#}）；backend/worker 分侧模式按端口区分
# （STUB_BACKEND_PORT/STUB_WORKER_PORT，默认 8000/8787——分侧断言的
# 用例都跑默认端口）。STUB_CURL_LOG=yes 时把完整调用行记入 STUB_LOG
# （默认不记，保持日志聚焦进程启动序列）。
_CURL_STUB = """#!/usr/bin/env bash
mode="${STUB_CURL_OK:-yes}"
url="${!#}"
if [[ "${STUB_CURL_LOG:-}" == "yes" ]]; then echo "curl $*" >> "${STUB_LOG}"; fi
case "$mode" in
  no) exit 7 ;;
  backend) [[ "$url" == *":${STUB_BACKEND_PORT:-8000}/api/health" ]] && exit 0; exit 7 ;;
  worker) [[ "$url" == *":${STUB_WORKER_PORT:-8787}/api/health" ]] && exit 0; exit 7 ;;
  *) exit 0 ;;
esac
"""

# sleep 桩：no-op（健康等待 150 次 × 2s 瞬间跑完，失败路径无需真实等待）。
_SLEEP_STUB = """#!/usr/bin/env bash
exit 0
"""

_CAFFEINATE_STUB = """#!/usr/bin/env bash
echo "caffeinate $*" >> "${STUB_LOG}"
exit 0
"""


def _write_stub(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    """合成仓库布局：真实 native-prod-up.sh / native-prod-down.sh（含其
    source 的 scripts/lib/dotenv.sh，#486 收敛）、桩决策脚本、前端目录与
    PATH 桩。"""
    main = tmp_path / "main"
    (main / "scripts" / "lib").mkdir(parents=True)
    (main / "frontend" / "node_modules").mkdir(parents=True)
    (main / "deploy").mkdir()
    (main / "data").mkdir()
    shutil.copy(UP_SCRIPT, main / "scripts" / UP_SCRIPT.name)
    shutil.copy(DOWN_SCRIPT, main / "scripts" / DOWN_SCRIPT.name)
    shutil.copy(ROOT / "scripts" / "lib" / "dotenv.sh", main / "scripts" / "lib" / "dotenv.sh")
    _write_stub(main / "scripts" / "local-s3-decide.sh", _DECIDE_STUB)
    _write_stub(main / "scripts" / "ensure-velites.sh", _ENSURE_VELITES_STUB)
    (main / "deploy" / "uvicorn-log-config.json").write_text("{}\n")
    (main / ".env").write_text("")
    # .venv/bin/python：uv 桩不会真的 sync 出来，预置为记录桩——nohup
    # 启动的服务进程参数（--host/--port 等）经它全数落日志。
    venv_bin = main / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    _write_stub(venv_bin / "python", _PYTHON_STUB)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_stub(bin_dir / "lsof", _LSOF_STUB)
    _write_stub(bin_dir / "npm", _NPM_STUB)
    _write_stub(bin_dir / "uv", _UV_STUB)
    _write_stub(bin_dir / "caffeinate", _CAFFEINATE_STUB)
    _write_stub(bin_dir / "docker", _DOCKER_STUB)
    _write_stub(bin_dir / "curl", _CURL_STUB)
    _write_stub(bin_dir / "sleep", _SLEEP_STUB)
    return main, bin_dir


def _run(
    main: Path,
    bin_dir: Path,
    stub_log: Path,
    extra_env: dict[str, str] | None = None,
    script: str = "native-prod-up.sh",
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    # 清掉测试运行者环境里的同名变量：NATIVE_* 是两级来源的高优先级层，
    # AGENT_LEGION_LOCAL_S3* 会经 decide 桩外泄，STUB_* 会污染桩行为。
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("NATIVE_", "AGENT_LEGION_", "STUB_"))
    }
    env["PATH"] = f"{bin_dir}:/usr/bin:/bin"
    env["STUB_LOG"] = str(stub_log)
    env["STUB_FRONTEND_DIST"] = str(main / "frontend" / "dist")
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(main / "scripts" / script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def _log_lines(stub_log: Path) -> list[str]:
    return stub_log.read_text().splitlines()


# --- 1. 正常启动路径 ---


def test_normal_start_assembles_process_args(tmp_path: Path) -> None:
    """正常启动（无既有监听、健康即绿）：uvicorn 与 worker.service 的
    --host/--port 按四变量组装，.env 提供的值（#486 两级来源）贯通到
    进程参数；caffeinate 包裹、日志重定向链路照常；脚本级 exit 0。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    (main / ".env").write_text(
        "NATIVE_BACKEND_PORT=9001\nNATIVE_BACKEND_BIND=192.0.2.1\n"
        "NATIVE_WORKER_PORT=9101\nNATIVE_WORKER_BIND=127.0.0.1\n"
    )

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    lines = _log_lines(stub_log)
    uvicorn_line = next(line for line in lines if "uvicorn" in line)
    assert "server.app.main:create_prod_app" in uvicorn_line
    assert "--host 192.0.2.1" in uvicorn_line
    assert "--port 9001" in uvicorn_line
    assert "--factory" in uvicorn_line
    worker_line = next(line for line in lines if "worker.service" in line)
    assert "--host 127.0.0.1" in worker_line
    assert "--port 9101" in worker_line
    assert "--state-dir data/agent-worker-service" in worker_line
    # caffeinate 防睡眠包裹在两个进程上（-is 参数）。
    assert any(line.startswith("caffeinate -is") for line in lines)
    # velites 目录前置链路（ensure-velites.sh --print-bin-dir，单一事实源）。
    assert "ensure-velites --print-bin-dir" in "\n".join(lines)
    assert "原生环境已就绪" in result.stdout
    assert "后端 http://192.0.2.1:9001" in result.stdout
    assert "Worker 控制台 http://127.0.0.1:9101" in result.stdout
    # 前端构建与依赖同步链路照常走桩。
    assert "npm run build" in "\n".join(lines)
    assert "uv sync --frozen" in "\n".join(lines)


def test_env_override_beats_dotenv_in_full_execution(tmp_path: Path) -> None:
    """进程环境覆盖 .env（#486 三态之「环境覆盖」，整体执行验证）：临时
    覆盖逃生门对四个变量都生效，bind 通配形态正确贯通（0.0.0.0）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    (main / ".env").write_text(
        "NATIVE_BACKEND_PORT=9001\nNATIVE_BACKEND_BIND=192.0.2.1\n"
        "NATIVE_WORKER_PORT=9101\nNATIVE_WORKER_BIND=127.0.0.1\n"
    )

    result = _run(
        main,
        bin_dir,
        stub_log,
        {
            "NATIVE_BACKEND_PORT": "9202",
            "NATIVE_BACKEND_BIND": "0.0.0.0",
            "NATIVE_WORKER_PORT": "9303",
            "NATIVE_WORKER_BIND": "192.0.2.7",
        },
    )

    assert result.returncode == 0, result.stderr
    lines = _log_lines(stub_log)
    uvicorn_line = next(line for line in lines if "uvicorn" in line)
    worker_line = next(line for line in lines if "worker.service" in line)
    assert "--host 0.0.0.0" in uvicorn_line
    assert "--port 9202" in uvicorn_line
    assert "--host 192.0.2.7" in worker_line
    assert "--port 9303" in worker_line
    # 健康探测地址按通配 bind 归一（0.0.0.0 → 127.0.0.1）。
    assert "后端 http://127.0.0.1:9202" in result.stdout


def test_defaults_when_env_and_dotenv_absent(tmp_path: Path) -> None:
    """#486 三态之「两者皆缺回落默认」（整体执行验证）：.env 空文件 +
    进程环境无 NATIVE_* → 8000/8787 与双 127.0.0.1，与历史行为一致。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    lines = _log_lines(stub_log)
    uvicorn_line = next(line for line in lines if "uvicorn" in line)
    worker_line = next(line for line in lines if "worker.service" in line)
    assert "--host 127.0.0.1" in uvicorn_line
    assert "--port 8000" in uvicorn_line
    assert "--host 127.0.0.1" in worker_line
    assert "--port 8787" in worker_line


# --- 2. 幂等分支 ---


def test_idempotent_skip_when_matching_listener_exists(tmp_path: Path) -> None:
    """幂等分支（命中路径）：STUB_LISTENERS 报告请求的 display:port 在
    监听 → 「已在运行，跳过」，进程不启动（日志无 uvicorn/worker 参数行）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(
        main,
        bin_dir,
        stub_log,
        {"STUB_LISTENERS": "127.0.0.1:8000,127.0.0.1:8787"},
    )

    assert result.returncode == 0, result.stderr
    lines = _log_lines(stub_log)
    assert "后端已在 :8000 运行，跳过" in result.stdout
    assert "Worker 已在 :8787 运行，跳过" in result.stdout
    assert not any("uvicorn" in line for line in lines)
    assert not any("worker.service" in line for line in lines)
    assert "原生环境已就绪" in result.stdout


def test_idempotent_miss_starts_processes(tmp_path: Path) -> None:
    """幂等分支（未命中路径）：无既有监听 → 照常启动两个进程（对照组，
    钉住 skip 只作用于「命中」分支）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log, {"STUB_LISTENERS": ""})

    assert result.returncode == 0, result.stderr
    lines = _log_lines(stub_log)
    assert any("uvicorn" in line for line in lines)
    assert any("worker.service" in line for line in lines)
    assert "启动后端 127.0.0.1:8000" in result.stdout
    assert "启动 Worker 127.0.0.1:8787" in result.stdout


def test_wildcard_bind_skips_over_specific_listener(tmp_path: Path) -> None:
    """通配 bind 幂等兜底（#486/#482 follow-up，行为级）：请求 0.0.0.0 而
    lsof 只报具体地址 127.0.0.1:8000（port_listening 判否——通配归一为 *
    后不匹配具体地址行），同端口同族仍有任意监听 → 跳过启动并打醒目提示
    （不并行起第二个实例连同一库）。Worker 无监听照常启动（对照）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(
        main,
        bin_dir,
        stub_log,
        {"STUB_LISTENERS": "127.0.0.1:8000", "NATIVE_BACKEND_BIND": "0.0.0.0"},
    )

    assert result.returncode == 0, result.stderr
    lines = _log_lines(stub_log)
    assert not any("uvicorn" in line for line in lines), "通配兜底未拦截后端启动"
    assert any("worker.service" in line for line in lines)
    assert "不并行启动第二个实例" in result.stderr
    assert "单副本退化" in result.stderr
    assert "./scripts/native-prod-down.sh" in result.stderr
    assert "后端视为已在 :8000 运行，跳过" in result.stdout


def test_wildcard_bind_starts_when_port_really_free(tmp_path: Path) -> None:
    """通配 bind + 端口确实空闲：兜底不误伤（STUB_LISTENERS 空 →
    port_has_any_listener 也判否）→ 照常启动。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(
        main,
        bin_dir,
        stub_log,
        {"STUB_LISTENERS": "", "NATIVE_BACKEND_BIND": "0.0.0.0"},
    )

    assert result.returncode == 0, result.stderr
    assert any("uvicorn" in line for line in _log_lines(stub_log))
    assert "启动后端 0.0.0.0:8000" in result.stdout


def test_specific_bind_skips_over_wildcard_listener(tmp_path: Path) -> None:
    """反向边界（请求具体 bind + 已有通配监听）：port_listening 的通配
    模式（*:port）命中 → 跳过启动（现状语义，#486 确认并钉死）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(
        main,
        bin_dir,
        stub_log,
        {"STUB_LISTENERS": "*:8000"},
    )

    assert result.returncode == 0, result.stderr
    lines = _log_lines(stub_log)
    assert not any("uvicorn" in line for line in lines)
    assert "后端已在 :8000 运行，跳过" in result.stdout


# --- 3. 健康检查循环 ---


def test_health_loop_times_out_with_exit_1(tmp_path: Path) -> None:
    """健康检查失败路径：curl 桩恒败（STUB_CURL_OK=no），150 次循环
    瞬间跑完（sleep 桩 no-op），超时退出码 1 且日志指引正确。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log, {"STUB_CURL_OK": "no"})

    assert result.returncode == 1
    assert "服务未在预期时间内就绪" in result.stderr
    assert "data/logs/prod-{backend,worker}.log" in result.stderr


def test_health_loop_progress_reported_while_waiting(tmp_path: Path) -> None:
    """等待期间每 15 次循环（30s）输出一次进度：backend_ok/worker_ok
    分侧状态可见，避免误报启动失败（#127）。curl 桩让后端绿、worker
    恒败，验证进度行如实反映分侧状态后超时退出。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log, {"STUB_CURL_OK": "backend"})

    assert result.returncode == 1
    assert "backend_ok=true worker_ok=false" in result.stdout


def test_health_probe_curl_arguments(tmp_path: Path) -> None:
    """健康探测的 curl 参数组装：--noproxy '*'（本机探测绕过环境代理）、
    --fail（HTTP 2xx 才算就绪）、-m 2（超时上限）、-o /dev/null（丢弃
    响应体）、-sS；两个组件按各自派生的 host:port 探测。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"

    result = _run(main, bin_dir, stub_log, {"STUB_CURL_LOG": "yes"})

    assert result.returncode == 0, result.stderr
    curls = [line for line in _log_lines(stub_log) if line.startswith("curl ")]
    assert curls, "curl 桩未记录任何调用"
    for line in curls:
        # 脚本里的 '*' 引号是 shell 层的，argv 传到桩已是裸 *。
        assert "--noproxy *" in line
        assert "--fail" in line
        assert "-m 2" in line
        assert "-o /dev/null" in line
        assert "-sS" in line
    assert any("http://127.0.0.1:8000/api/health" in line for line in curls)
    assert any("http://127.0.0.1:8787/api/health" in line for line in curls)


# --- 4. 警告块 ---


def test_specific_bind_with_loopback_host_url_warns(tmp_path: Path) -> None:
    """警告块（bind 具体网卡 + 本地 Worker 状态副本 host_url 指向
    loopback）：stderr 打 host_url 失配警告（含按 bind 派生的修正目标）
    与 Worker 控制台本机接入地址变更提示。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    worker_yaml = main / "data" / "agent-worker-service" / "worker.yaml"
    worker_yaml.parent.mkdir(parents=True, exist_ok=True)
    worker_yaml.write_text("host_url: http://127.0.0.1:8000\n")

    result = _run(
        main,
        bin_dir,
        stub_log,
        {
            "NATIVE_BACKEND_BIND": "192.0.2.1",
            "NATIVE_WORKER_BIND": "192.0.2.1",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "host_url 仍指向 loopback" in result.stderr
    assert "http://192.0.2.1:8000" in result.stderr
    assert "127.0.0.1 不再监听" in result.stderr
    assert "http://192.0.2.1:8787" in result.stderr


def test_loopback_bind_no_warnings(tmp_path: Path) -> None:
    """对照组：默认 loopback bind 不触发任何警告（worker.yaml 的
    loopback host_url 是正确配置）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    worker_yaml = main / "data" / "agent-worker-service" / "worker.yaml"
    worker_yaml.parent.mkdir(parents=True, exist_ok=True)
    worker_yaml.write_text("host_url: http://127.0.0.1:8000\n")

    result = _run(main, bin_dir, stub_log)

    assert result.returncode == 0, result.stderr
    assert "警告" not in result.stderr
    assert "提示" not in result.stderr


# --- 5. 函数悬空引用防护 ---
#
# 整体执行本身即防护：脚本内任何被调用的函数缺失定义时 bash 直接报
# 「command not found」，set -e 下立即非零退出——上面所有用例的
# returncode 断言同时钉住「无悬空引用」（#480/#482 期间 listener_display
# 被误删那类断裂会让全部用例集体翻红，而不是靠人工 grep 撞见）。本节
# 的多字节标点用例是首跑抓到的真实断裂（见模块 docstring）：裸 $VAR
# 紧跟 U+FF0C/U+FF08 时 bash 把标点首字节误并入变量名，set -u 下对已
# 赋值变量报 unbound variable、启动路径退出 1——test_specific_bind_
# with_loopback_host_url_warns 的 returncode 断言整体执行覆盖它，此处
# 再以形态断言钉死「提示文案变量必须花括号」，防退回裸形态。


def test_prompt_text_uses_braced_variables_before_multibyte_punctuation() -> None:
    """提示/警告文案里的变量一律 ${VAR}：裸 $VAR 紧跟多字节标点（，与
    （）会被 bash 把标点首字节误并入变量名——set -u 下报 unbound
    variable、启动路径整体断裂（#484 首跑抓到的真实 bug）。"""
    up = UP_SCRIPT.read_text(encoding="utf-8")
    for line in up.splitlines():
        if line.lstrip().startswith(('echo "警告', 'echo "提示')):
            bare = re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", line)
            if bare:
                pytest.fail(f"提示文案存在裸 $VAR（应使用花括号）: {line.strip()}")


def test_down_uses_dotenv_two_level_source(tmp_path: Path) -> None:
    """down 的四变量两级来源（#486，整体执行验证）：.env 提供端口/bind，
    无监听 → 全部跳过、rc 0；文案按 .env 的 bind/port 呈现。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    (main / ".env").write_text(
        "NATIVE_BACKEND_PORT=9001\nNATIVE_BACKEND_BIND=192.0.2.1\n"
        "NATIVE_WORKER_PORT=9101\nNATIVE_WORKER_BIND=127.0.0.1\n"
    )

    result = _run(main, bin_dir, stub_log, script="native-prod-down.sh")

    assert result.returncode == 0, result.stderr
    assert "Worker 127.0.0.1:9101 未在运行，跳过" in result.stdout
    assert "后端 192.0.2.1:9001 未在运行，跳过" in result.stdout


def test_down_wildcard_residual_listener_fails_loudly(tmp_path: Path) -> None:
    """down 的通配 bind 残留态（#486）：请求 0.0.0.0 而 lsof 只报具体地址
    监听（listener_pids 的 display=* 不命中具体地址行）→ 不得伪装成功，
    改判 rc 1 并指引（与 up 的通配兜底配套：用户按提示来重启，静默 0
    会让旧实例永远停不掉）。"""
    main, bin_dir = _setup(tmp_path)
    stub_log = tmp_path / "stub.log"
    (main / ".env").write_text("NATIVE_BACKEND_BIND=0.0.0.0\n")

    result = _run(
        main,
        bin_dir,
        stub_log,
        {"STUB_LISTENERS": "127.0.0.1:8000", "STUB_PID": "555"},
        script="native-prod-down.sh",
    )

    assert result.returncode == 1
    assert "绑定形态与 0.0.0.0 不同" in result.stderr
    assert "未停止" in result.stderr
