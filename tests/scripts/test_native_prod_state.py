"""原生 prod 运行态记录（#894）的行为检查。

prod-up 把实际起来的实例（PID + bind/port）落到 ``data/native-prod.state``，
prod-down 以它为准、配置为辅；按记录 kill 前校验进程签名、``--port`` 与
工作目录（防 PID 复用误杀）。这里在临时目录里搭假仓库根（只拷三个脚本），
用带服务签名命令行的 Python 监听进程充当后端/Worker，真实执行
``native-prod-down.sh`` 与 up 的守卫函数——不碰本机真实 prod 实例。
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = ("native-prod-down.sh", "dotenv-lib.sh", "native-prod-state-lib.sh")
_NATIVE_VARS = (
    "NATIVE_BACKEND_PORT",
    "NATIVE_WORKER_PORT",
    "NATIVE_BACKEND_BIND",
    "NATIVE_WORKER_BIND",
)
_SIGNATURES = {
    "backend": ["-m", "uvicorn", "server.app.main:create_prod_app", "--factory"],
    "worker": ["-m", "worker.service", "--state-dir", "data/agent-worker-service"],
}
# argv 尾部 [..., "--host", host, "--port", port]：与真实启动命令同形。
# 启动后尚未监听的服务（R2：nohup 之后、bind 之前被中断）：只睡不监听。
_NOT_LISTENING = "import time\ntime.sleep(600)\n"
_LISTENER = (
    "import socket, sys, time\n"
    "fam = socket.AF_INET6 if ':' in sys.argv[-3] else socket.AF_INET\n"
    "s = socket.socket(fam); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
    "s.bind((sys.argv[-3], int(sys.argv[-1]))); s.listen(1)\n"
    "time.sleep(600)\n"
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "data").mkdir()
    for name in _SCRIPTS:
        shutil.copy(ROOT / "scripts" / name, root / "scripts" / name)
    return root


class _Proc:
    """脱离 pytest 的假服务进程（经中间 shell 孤儿化，由 init 收尸）。

    直接 Popen 的子进程被 SIGTERM 后在 pytest 回收前是僵尸，``kill -0``
    仍成功，down 会误判「未退出」；孤儿化后退出即被 init 回收。"""

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def alive(self) -> bool:
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        return True

    def kill(self) -> None:
        if self.alive():
            os.kill(self.pid, signal.SIGKILL)


@pytest.fixture
def procs() -> Iterator[list[_Proc]]:
    started: list[_Proc] = []
    yield started
    for proc in started:
        proc.kill()


def _detach(procs: list[_Proc], cwd: Path, argv: list[str]) -> _Proc:
    # 子进程关掉管道写端（否则读端要等假服务退出才见 EOF）。
    launcher = 'exec </dev/null >/dev/null 2>&1; "$@" {fd}>&- & echo $! >&{fd}'
    read_fd, write_fd = os.pipe()
    subprocess.run(
        ["bash", "-c", launcher.format(fd=write_fd), "launcher", *argv],
        cwd=cwd,
        check=True,
        pass_fds=(write_fd,),
    )
    os.close(write_fd)
    with os.fdopen(read_fd) as reader:
        proc = _Proc(int(reader.read().strip()))
    procs.append(proc)
    return proc


def _spawn(procs: list[_Proc], cwd: Path, kind: str, port: int, host: str = "127.0.0.1") -> _Proc:
    argv = [sys.executable, "-c", _LISTENER, *_SIGNATURES[kind]]
    proc = _detach(procs, cwd, [*argv, "--host", host, "--port", str(port)])
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with socket.socket(family) as sock:
            if sock.connect_ex((host, port)) == 0:
                return proc
        time.sleep(0.05)
    raise AssertionError(f"fake {kind} did not listen on {port}")


def _write_state(repo: Path, **values: object) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    (repo / "data" / "native-prod.state").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_config(repo: Path, backend_port: int, worker_port: int) -> None:
    (repo / ".env").write_text(
        f"NATIVE_BACKEND_PORT={backend_port}\nNATIVE_WORKER_PORT={worker_port}\n", encoding="utf-8"
    )


def _run_down(repo: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _NATIVE_VARS}
    return subprocess.run(
        ["bash", str(repo / "scripts" / "native-prod-down.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _exited(proc: _Proc) -> bool:
    deadline = time.monotonic() + 5
    while proc.alive():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def test_down_stops_recorded_instance_after_config_changed(repo: Path, procs: list) -> None:
    """#894 主场景：实例在旧端口运行、配置已改成新端口——down 按运行态记录
    停掉旧实例（不再误报未运行），全部停下后删除记录。"""
    old_b, old_w = _free_port(), _free_port()
    backend = _spawn(procs, repo, "backend", old_b)
    worker = _spawn(procs, repo, "worker", old_w)
    _write_state(
        repo,
        BACKEND_PID=backend.pid,
        BACKEND_BIND="127.0.0.1",
        BACKEND_PORT=old_b,
        WORKER_PID=worker.pid,
        WORKER_BIND="127.0.0.1",
        WORKER_PORT=old_w,
    )
    _write_config(repo, _free_port(), _free_port())

    result = _run_down(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _exited(backend) and _exited(worker)
    assert "按运行态记录停止" in result.stdout
    assert not (repo / "data" / "native-prod.state").exists()


@pytest.mark.parametrize("recorded_pid", ["dead", "empty"])
def test_down_finds_instance_on_recorded_port_when_pid_stale(
    repo: Path, procs: list, recorded_pid: str
) -> None:
    """记录 PID 已失效（实例被别的方式重启过），或为空（up 刚 nohup 子进程、
    尚未监听就被中断——R1 finding：启动后立即落的记录此时只有地址）：记录的
    bind:port 上仍有签名匹配的本实例监听时，按运行态停它，而不是回落到已改
    的配置。"""
    old_b, old_w = _free_port(), _free_port()
    backend = _spawn(procs, repo, "backend", old_b)
    pid: object = ""
    if recorded_pid == "dead":
        dead = subprocess.Popen(["true"])
        dead.wait()
        pid = dead.pid
    _write_state(repo, BACKEND_PID=pid, BACKEND_BIND="127.0.0.1", BACKEND_PORT=old_b)
    _write_config(repo, _free_port(), old_w)

    result = _run_down(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _exited(backend)


def test_down_never_kills_reused_pid(repo: Path, procs: list, tmp_path: Path) -> None:
    """PID 复用防护：记录 PID 现属无签名的无关进程、或签名相同但工作目录是
    另一个仓库根（别的 worktree 的实例）——都不得 kill；记录视为陈旧，提示
    后回落按配置定位。"""
    stray = _detach(procs, repo, ["sleep", "300"])
    other_root = tmp_path / "other-worktree"
    other_root.mkdir()
    rec_b, rec_w = _free_port(), _free_port()
    foreign_worker = _spawn(procs, other_root, "worker", rec_w)
    _write_state(
        repo,
        BACKEND_PID=stray.pid,
        BACKEND_BIND="127.0.0.1",
        BACKEND_PORT=rec_b,
        WORKER_PID=foreign_worker.pid,
        WORKER_BIND="127.0.0.1",
        WORKER_PORT=rec_w,
    )
    _write_config(repo, _free_port(), _free_port())

    result = _run_down(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert stray.alive() and foreign_worker.alive()
    assert result.stdout.count("回落按当前配置") == 2
    assert "未在运行，跳过" in result.stdout
    # R2：记录 PID 仍存活却无法确认身份——不发信号，也不删记录。
    assert "无法确认属于本实例" in result.stderr
    assert (repo / "data" / "native-prod.state").exists()


def test_down_without_record_falls_back_to_config(repo: Path, procs: list) -> None:
    """无运行态记录（旧版本 up 起的实例）：提示后按配置定位，保持既有行为。"""
    port_b, port_w = _free_port(), _free_port()
    backend = _spawn(procs, repo, "backend", port_b)
    _write_config(repo, port_b, port_w)

    result = _run_down(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "无运行态记录" in result.stdout
    assert _exited(backend)


def _lib_call(repo: Path, snippet: str) -> subprocess.CompletedProcess[str]:
    up = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")
    match = re.search(
        r"^refuse_recorded_instance_elsewhere\(\) \{.*?^\}", up, re.MULTILINE | re.DOTALL
    )
    assert match, "refuse_recorded_instance_elsewhere 函数定义缺失"
    code = (
        "set -euo pipefail\n"
        f'ROOT="{repo}"\n'
        f'source "{repo}/scripts/native-prod-state-lib.sh"\n' + match.group(0) + "\n" + snippet
    )
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=60)


def test_up_refuses_when_recorded_instance_runs_elsewhere(repo: Path, procs: list) -> None:
    """up 侧守卫：记录中的本实例仍在旧地址运行而配置已改 → 拒绝启动并指引
    先 down；配置未变（幂等重跑）或记录陈旧都放行。"""
    old_b = _free_port()
    backend = _spawn(procs, repo, "backend", old_b)
    _write_state(repo, BACKEND_PID=backend.pid, BACKEND_BIND="127.0.0.1", BACKEND_PORT=old_b)
    new_b = _free_port()
    guard = 'refuse_recorded_instance_elsewhere backend BACKEND "后端" 127.0.0.1 {}\n'

    refused = _lib_call(repo, guard.format(new_b))
    assert refused.returncode == 1
    assert f"127.0.0.1:{old_b}" in refused.stderr and "make prod-down" in refused.stderr
    assert _lib_call(repo, guard.format(old_b)).returncode == 0

    backend.kill()
    assert _exited(backend)
    assert _lib_call(repo, guard.format(new_b)).returncode == 0


def test_state_write_records_listener_pid_and_address(repo: Path, procs: list) -> None:
    """up 落的记录：PID 取端口上签名匹配的实际监听进程，bind/port 原样记下；
    没起来的服务 PID 留空。"""
    port_b, port_w = _free_port(), _free_port()
    backend = _spawn(procs, repo, "backend", port_b)
    result = _lib_call(
        repo, f'native_state_write "$ROOT" 127.0.0.1 {port_b} "" 0.0.0.0 {port_w} ""\n'
    )
    assert result.returncode == 0, result.stderr
    state = (repo / "data" / "native-prod.state").read_text(encoding="utf-8")
    assert f"BACKEND_PID={backend.pid}\n" in state
    assert f"BACKEND_PORT={port_b}\n" in state
    assert "WORKER_PID=\n" in state and "WORKER_BIND=0.0.0.0\n" in state


def _down_call(repo: Path, snippet: str) -> subprocess.CompletedProcess[str]:
    down = (ROOT / "scripts" / "native-prod-down.sh").read_text(encoding="utf-8")
    funcs = []
    for name in ("stop_pid", "stop_recorded_pid"):
        match = re.search(rf"^{name}\(\) \{{.*?^\}}", down, re.MULTILINE | re.DOTALL)
        assert match, f"{name} 函数定义缺失"
        funcs.append(match.group(0))
    code = (
        "set -euo pipefail\n"
        f'ROOT="{repo}"\n'
        f'source "{repo}/scripts/native-prod-state-lib.sh"\n' + "\n".join(funcs) + "\n" + snippet
    )
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=60)


def test_kill_reverifies_identity_right_before_signal(repo: Path, procs: list) -> None:
    """R1 finding（TOCTOU）：定位到 kill 之间实例退出且 PID 被复用时，kill
    紧前的复核不通过——在记录地址上重新定位，找不到就不发任何信号；找得到
    （实例换了 PID 仍在记录地址）则停新定位到的本实例。"""
    port = _free_port()
    reused = _detach(procs, repo, ["sleep", "300"])  # 模拟 PID 已被无关进程复用
    call = 'stop_recorded_pid backend {} 127.0.0.1 {} "后端" 5\n'

    skipped = _down_call(repo, call.format(reused.pid, port))
    assert skipped.returncode == 0, skipped.stderr
    assert "已退出，跳过" in skipped.stdout
    assert reused.alive()

    backend = _spawn(procs, repo, "backend", port)
    relocated = _down_call(repo, call.format(reused.pid, port))
    assert relocated.returncode == 0, relocated.stderr
    assert f"pid {backend.pid}" in relocated.stdout
    assert _exited(backend) and reused.alive()


def test_state_pid_and_address_come_from_same_listener(repo: Path, procs: list) -> None:
    """R1 finding：同端口另一地址上已有本 worktree 的同类旧实例（先起、PID
    更小）时，记录的 PID 必须是监听所记 bind 的那个进程，不能把旧实例的
    PID 与新地址拼成一条记录。"""
    port = _free_port()
    old = _spawn(procs, repo, "backend", port, host="::1")
    new = _spawn(procs, repo, "backend", port, host="127.0.0.1")
    result = _lib_call(repo, f'native_state_write "$ROOT" 127.0.0.1 {port} "" 127.0.0.1 1 ""\n')
    assert result.returncode == 0, result.stderr
    state = (repo / "data" / "native-prod.state").read_text(encoding="utf-8")
    assert f"BACKEND_PID={new.pid}\n" in state
    assert f"BACKEND_PID={old.pid}\n" not in state


def test_up_records_state_right_after_each_launch() -> None:
    """R1 finding 接线钉：每个 nohup 子进程起来后立即落记录（早于健康等待），
    健康等待期间被中断也不丢实例地址。"""
    up = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")
    write = 'native_state_write "$ROOT" "$BACKEND_BIND"'
    backend_start = up.index("> data/logs/prod-backend.log 2>&1 &")
    worker_start = up.index("> data/logs/prod-worker.log 2>&1 &")
    health_loop = up.index("for i in $(seq 1 150)")
    after_backend = up.index(write, backend_start)
    after_worker = up.index(write, worker_start)
    assert after_backend < worker_start
    assert worker_start < after_worker < health_loop


def test_down_stops_launched_instance_before_it_listens(repo: Path, procs: list) -> None:
    """R2 finding：up 在 nohup 之后、服务开始监听之前被中断，记录里只有启动
    PID；操作者随后改了配置再 down——仍须按记录 PID + 签名 + 工作目录停掉
    它（不依赖监听 socket），而不是回落新配置后删记录、留旧进程稍后起来。"""
    old_b, old_w = _free_port(), _free_port()
    argv = [sys.executable, "-c", _NOT_LISTENING, *_SIGNATURES["backend"]]
    pending = _detach(procs, repo, [*argv, "--host", "127.0.0.1", "--port", str(old_b)])
    deadline = time.monotonic() + 10
    while (
        "server.app.main"
        not in subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pending.pid)], capture_output=True, text=True
        ).stdout
    ):
        assert time.monotonic() < deadline, "fake backend did not exec"
        time.sleep(0.05)
    result = _lib_call(
        repo,
        f'native_state_write "$ROOT" 127.0.0.1 {old_b} {pending.pid} 127.0.0.1 {old_w} ""\n',
    )
    assert result.returncode == 0, result.stderr
    state = (repo / "data" / "native-prod.state").read_text(encoding="utf-8")
    assert f"BACKEND_PID={pending.pid}\n" in state
    _write_config(repo, _free_port(), _free_port())

    down = _run_down(repo)

    assert down.returncode == 0, down.stdout + down.stderr
    assert _exited(pending)
    assert "按运行态记录停止" in down.stdout
    assert not (repo / "data" / "native-prod.state").exists()


def test_up_records_launch_pid_and_side_attaches_caffeinate() -> None:
    """R2 接线钉：服务进程直接 nohup（不经 caffeinate 包装），$! 即服务 PID 并
    随即写入记录；caffeinate 以 -w 旁挂防睡眠。"""
    up = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")
    assert "nohup ${CAFFEINATE" not in up
    assert 'nohup "$CAFFEINATE" -is -w "$1"' in up
    for kind, log in (("BACKEND", "prod-backend.log"), ("WORKER", "prod-worker.log")):
        start = up.index(f"> data/logs/{log} 2>&1 &")
        assert up.index(f"{kind}_LAUNCH_PID=$!", start) < up.index("native_state_write", start)
    assert up.count('"$BACKEND_LAUNCH_PID" "$WORKER_BIND" "$WORKER_PORT" "$WORKER_LAUNCH_PID"') == 4


def _up_function_call(repo: Path, name: str, snippet: str) -> subprocess.CompletedProcess[str]:
    up = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{.*?^\}}", up, re.MULTILINE | re.DOTALL)
    assert match, f"{name} 函数定义缺失"
    code = (
        "set -euo pipefail\n"
        f'ROOT="{repo}"\n'
        f'source "{repo}/scripts/native-prod-state-lib.sh"\n' + match.group(0) + "\n" + snippet
    )
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, timeout=60)


def test_rerun_with_same_config_adopts_pending_instance(repo: Path, procs: list) -> None:
    """R3 finding：上次 up 在 nohup 之后、监听之前被中断，同配置重跑时记录中
    的本实例虽未监听也视为已在运行（交给健康等待），不得再 nohup 第二个
    进程；配置改了地址则不认领（由 refuse_recorded_instance_elsewhere 拒绝）。"""
    port_b = _free_port()
    argv = [sys.executable, "-c", _NOT_LISTENING, *_SIGNATURES["backend"]]
    pending = _detach(procs, repo, [*argv, "--host", "127.0.0.1", "--port", str(port_b)])
    deadline = time.monotonic() + 10
    while (
        "server.app.main"
        not in subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pending.pid)], capture_output=True, text=True
        ).stdout
    ):
        assert time.monotonic() < deadline, "fake backend did not exec"
        time.sleep(0.05)
    _write_state(repo, BACKEND_PID=pending.pid, BACKEND_BIND="127.0.0.1", BACKEND_PORT=port_b)
    call = "recorded_pending_pid backend BACKEND 127.0.0.1 {}\n"

    same = _up_function_call(repo, "recorded_pending_pid", call.format(port_b))
    assert same.returncode == 0, same.stderr
    assert same.stdout.strip() == str(pending.pid)
    moved = _up_function_call(repo, "recorded_pending_pid", call.format(_free_port()))
    assert moved.returncode == 0 and moved.stdout.strip() == ""

    pending.kill()
    assert _exited(pending)
    gone = _up_function_call(repo, "recorded_pending_pid", call.format(port_b))
    assert gone.returncode == 0 and gone.stdout.strip() == ""


def test_up_never_relaunches_pending_recorded_instance() -> None:
    """R3 接线钉：认领未就绪记录实例的分支在 nohup 分支之前，且认领值读在
    任何记录覆盖之前；沿用的 PID 作为启动 PID 继续写入记录，超时提示先 down。"""
    up = (ROOT / "scripts" / "native-prod-up.sh").read_text(encoding="utf-8")
    first_write = up.index('native_state_write "$ROOT"')
    for kind, label in (("BACKEND", "后端"), ("WORKER", "Worker")):
        read = up.index(f'{kind}_PENDING_PID="$(recorded_pending_pid')
        branch = up.index(f'elif [[ -n "${kind}_PENDING_PID" ]]; then')
        adopt = up.index(f'{kind}_LAUNCH_PID="${kind}_PENDING_PID"', branch)
        nohup = up.index("nohup .venv/bin/python", branch)
        assert read < first_write and read < branch < adopt < nohup, label
    assert "请先 make prod-down 按运行态记录停掉它" in up
