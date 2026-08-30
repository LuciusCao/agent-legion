"""Process supervision for the Agent Worker Service (config lives in worker/config_store)."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any

from yaml import YAMLError

from worker.config_store import WorkerConfigStore, public_config, validate_config
from worker.metrics_cache import METRICS_FILENAME
from worker.orphan_reaper import reap_orphaned_agents
from worker.registration.token import registration_tokens
from worker.restart_policy import (
    _EXIT_REFUSED,
    _KILL_WAIT,
    _RESTART_BACKOFF_INITIAL,
    _RESTART_BACKOFF_MAX,
    _STABLE_AFTER,
    _STOP_GRACE_MAX,
    restart_delay,
)
from worker.status import ENV_VAR, STATUS_FILENAME, read_runtime_status
from worker.status.projection import host_view, process_snapshot, status_payload
from worker.token_status import token_status

__all__ = ["WorkerConfigStore", "WorkerSupervisor", "public_config", "validate_config"]

# 退避两端归位（#250）后经 _current_restart_delay 封装消费：monkeypatch 本
# 模块常量即命中（按值重绑救不了跨命名空间函数体引用——review on #257）。
# 其余四个常量无测试锚点，直接以 restart_policy 限定名消费、不重绑。
_RESTART_BACKOFF_INITIAL = _RESTART_BACKOFF_INITIAL
_RESTART_BACKOFF_MAX = _RESTART_BACKOFF_MAX


def _current_restart_delay(restart_count: int) -> float:
    return restart_delay(restart_count, initial=_RESTART_BACKOFF_INITIAL, cap=_RESTART_BACKOFF_MAX)


class WorkerSupervisor:
    """Run the existing Worker runtime as a managed child process."""

    def __init__(self, store: WorkerConfigStore, worker_script: Path) -> None:
        self.store = store
        self.worker_script = worker_script
        self._lock = threading.RLock()  # 保护进程状态字段
        self._op_lock = threading.Lock()  # 串行化 start/stop/restart（含停止等待）
        self._process: subprocess.Popen[str] | None = None
        self._logs: deque[str] = deque(maxlen=500)
        self._started_at: float | None = None
        self._exit_code: int | None = None
        self._generation = 0
        self._shutdown = False
        self._restart_event = threading.Event()
        self._restart_count = 0
        self._next_restart_delay: float | None = None
        self._failed_reason: str | None = None
        self._warned_divergence = False

    def _log(self, message: str) -> None:
        """Append one panel log line with a local-time timestamp prefix."""
        self._logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")

    def start(self) -> None:
        with self._op_lock:
            self._shutdown = False
            self._failed_reason = None
            self._next_restart_delay = None
            self._restart_event.clear()
            self._start()

    def _start(self) -> None:
        with self._lock:
            if self.running() or not self.store.configured():
                return
            self.store.update_public({"claim_enabled": False})
            # 刻意设计（含崩溃自动重启路径）：重启后默认暂停认领，需人工重新打开。
            self._log("启动时已将 claim_enabled 重置为 false，需在控制台重新打开认领")
            config = self.store.read()
            tokens = registration_tokens(config, self.store.state_dir)
            if not tokens:
                directory = self.store.token_dir()
                self._log(f"配置等待中：没有可用的注册 Token（{directory} 为空）")
                return
            if not self._warned_divergence and self._mounted_config_diverged():
                self._warned_divergence = True
                self._log(
                    "警告：挂载的配置文件与本地状态副本不一致，挂载修改不会自动生效；"
                    "可用 workerctl configure 覆盖，或删除状态文件后重启以重新导入"
                )
            self._generation += 1
            generation = self._generation
            status_file = self.store.state_dir / STATUS_FILENAME
            self._cleanup_runtime_files()
            process = self._process = subprocess.Popen(
                [sys.executable, str(self.worker_script), "--config", str(self.store.path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, ENV_VAR: str(status_file)},
                text=True,
                bufsize=1,
            )
            self._started_at = time.time()
            self._exit_code = None
        # 锁外 reap（新进程尚在解释器启动阶段，clean_work_root 远落后于 killpg）。
        self._reap_orphans()
        threading.Thread(target=self._collect_logs, args=(process, generation), daemon=True).start()

    def stop(self) -> None:
        with self._op_lock:
            self._shutdown = True
            self._restart_event.set()  # 唤醒退避等待中的 collector
            self._stop_locked()

    def _stop_locked(self) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return
            try:
                config = self.store.read(require_identity=False)
                wait_seconds = min(float(config["shutdown_grace_seconds"]), _STOP_GRACE_MAX)
            except (OSError, ValueError, KeyError, TypeError, YAMLError):
                # stop 路径容忍配置损坏：能停进程即可（restart 的 _start 会再校验）。
                wait_seconds = _STOP_GRACE_MAX
            process.terminate()
        try:
            process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=_KILL_WAIT)
        if process.poll() is not None:
            self._cleanup_runtime_files()
            self._reap_orphans()  # 已出锁

    def _cleanup_runtime_files(self) -> None:
        for filename in (STATUS_FILENAME, METRICS_FILENAME):
            (self.store.state_dir / filename).unlink(missing_ok=True)

    def _reap_orphans(self) -> None:
        # 锁外兜底 killpg：每条残留记录 SIGTERM 后固定等 1s，持状态锁执行会
        # 卡住 status/stop 等 HTTP handler。配置损坏读不到 work_root 则跳过。
        with suppress(OSError, ValueError, KeyError, TypeError, YAMLError):
            work_root = Path(str(self.store.read(require_identity=False)["work_root"]))
            reap_orphaned_agents(work_root, self._log)

    def restart(self) -> None:
        # 操作锁把 stop（含等待）+ start 做成临界区，避免返回 200 但仍跑旧配置。
        with self._op_lock:
            self._shutdown = True
            self._restart_event.set()
            self._stop_locked()
            self._shutdown = False
            self._failed_reason = None
            self._restart_count = 0
            self._next_restart_delay = None
            self._restart_event.clear()
            self._start()

    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def _collect_logs(self, process: subprocess.Popen[str], generation: int) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            with self._lock:
                self._log(line.rstrip())
        exit_code = process.wait()
        # 锁外 reap（幂等）：仅当前 generation 的 executor 退出才有孤儿；过期
        # generation 说明新 executor 已启动，其 agent 记录归属它，不能动。
        if generation == self._generation:
            self._reap_orphans()
        with self._lock:
            self._log(f"Worker 执行进程已退出，退出码 {exit_code}")
            if generation == self._generation:
                self._cleanup_runtime_files()
            if generation != self._generation or self._shutdown:
                return
            self._exit_code = exit_code
            if exit_code == _EXIT_REFUSED:
                self._failed_reason = "Host 拒绝注册、Worker 已被吊销或启动预检失败（退出码 2），详见上方 Worker 日志，请修正配置后手动重启"
                self._log(self._failed_reason)
                return
            if self._started_at is not None and time.time() - self._started_at >= _STABLE_AFTER:
                self._restart_count = 0
            self._restart_count += 1
            delay = _current_restart_delay(self._restart_count)
            self._next_restart_delay = delay
            self._log(f"{delay:.0f} 秒后自动重启 Worker 执行进程（第 {self._restart_count} 次）")
        if self._restart_event.wait(delay):
            return
        with self._op_lock:
            with self._lock:
                if self._shutdown or generation != self._generation:
                    return
            self._start()

    def logs(self, limit: int = 200) -> list[str]:
        with self._lock:  # 锁内复制，避免迭代时被 collector append
            return list(self._logs)[-max(1, min(limit, 500)) :]

    def _mounted_config_diverged(self) -> bool:
        bootstrap = self.store.bootstrap_path
        if bootstrap is None or not bootstrap.is_file() or not self.store.path.is_file():
            return False
        try:
            mounted = hashlib.sha256(bootstrap.read_bytes()).digest()
            current = hashlib.sha256(self.store.path.read_bytes()).digest()
        except OSError:
            return False
        return mounted != current

    def status(self) -> dict[str, Any]:
        """本地与 Host 状态；connected 表示已登记且未被吊销，不代表实时连接。"""
        configured = self.store.configured()
        config = self.store.read(require_identity=False)
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            snapshot = process_snapshot(
                running,
                self._process.pid if running and self._process is not None else None,
                self._started_at,
                self._exit_code,
                self._restart_count,
                self._next_restart_delay,
                self._failed_reason,
            )
        runtime = read_runtime_status(self.store.state_dir / STATUS_FILENAME)
        executions = runtime["executions"]
        remote = host_view(
            configured, runtime["remote"] if configured else {}, snapshot["worker_running"]
        )
        return status_payload(
            configured,
            config,
            executions,
            self.store.bootstrap_error,
            self._mounted_config_diverged(),
            snapshot,
            remote,
        )

    def token_status(self) -> dict[str, str]:
        """Per-token registration state（投影逻辑在 worker/token_status.py）。"""
        with self._lock:
            failed = self._failed_reason is not None
        return token_status(failed, self.store.state_dir, self.store.read_registration_tokens)
