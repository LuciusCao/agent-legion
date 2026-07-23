"""Process supervision for the Agent Worker Service (config lives in agent_worker_config_store)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any

from worker.config_store import WorkerConfigStore, public_config, validate_config

__all__ = ["WorkerConfigStore", "WorkerSupervisor", "public_config", "validate_config"]

_EXIT_REFUSED = 2  # Host 拒绝注册 / Worker 被吊销：不自动重启，进入 failed
_RESTART_BACKOFF_INITIAL = 5.0
_RESTART_BACKOFF_MAX = 300.0
_STABLE_AFTER = 60.0  # 稳定运行超过该时长后重置退避
# 优雅停止宽限 clamp 到 22s、kill 后最多再等 3s，总预算 25s，低于 compose stop_grace_period 30s。
_STOP_GRACE_MAX = 22.0
_KILL_WAIT = 3.0


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
            config = self.store.read()
            token_file = Path(str(config["register_token_file"]))
            if not token_file.is_file():
                self._logs.append(f"配置等待中：注册密钥文件不存在：{token_file}")
                return
            if not self._warned_divergence and self._mounted_config_diverged():
                self._warned_divergence = True
                self._logs.append(
                    "警告：挂载的配置文件与本地状态副本不一致，挂载修改不会自动生效；"
                    "可用 workerctl configure 覆盖，或删除状态文件后重启以重新导入"
                )
            self._generation += 1
            generation = self._generation
            self._process = subprocess.Popen(
                [sys.executable, str(self.worker_script), "--config", str(self.store.path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._started_at = time.time()
            self._exit_code = None
            process = self._process
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
            config = self.store.read(require_identity=False)
            wait_seconds = min(float(config["shutdown_grace_seconds"]), _STOP_GRACE_MAX)
            process.terminate()
        try:
            process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=_KILL_WAIT)

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
                self._logs.append(line.rstrip())
        exit_code = process.wait()
        with self._lock:
            self._logs.append(f"Worker 执行进程已退出，退出码 {exit_code}")
            if generation != self._generation or self._shutdown:
                return
            self._exit_code = exit_code
            if exit_code == _EXIT_REFUSED:
                self._failed_reason = (
                    "Host 拒绝注册或 Worker 已被吊销（退出码 2），请修正配置后手动重启"
                )
                self._logs.append(self._failed_reason)
                return
            if self._started_at is not None and time.time() - self._started_at >= _STABLE_AFTER:
                self._restart_count = 0
            self._restart_count += 1
            delay = min(
                _RESTART_BACKOFF_INITIAL * (2 ** (self._restart_count - 1)),
                _RESTART_BACKOFF_MAX,
            )
            self._next_restart_delay = delay
            self._logs.append(
                f"{delay:.0f} 秒后自动重启 Worker 执行进程（第 {self._restart_count} 次）"
            )
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
        """本地与 Host 状态。connected 表示"已在 Host 登记且未被吊销"，不代表实时连接。"""
        configured = self.store.configured()
        config = self.store.read(require_identity=False)
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            snapshot = {
                "worker_running": running,
                "pid": self._process.pid if running and self._process is not None else None,
                "started_at": self._started_at,
                "exit_code": self._exit_code,
                "restart_count": self._restart_count,
                "next_restart_delay": self._next_restart_delay,
                "failed": self._failed_reason,
            }
        remote = _query_remote_status(config) if configured else {}
        return {
            "service": "running",
            "configured": configured,
            "bootstrap_error": self.store.bootstrap_error,
            "mounted_config_diverged": self._mounted_config_diverged(),
            **snapshot,
            **remote,
        }


def _query_remote_status(config: dict[str, Any]) -> dict[str, Any]:
    host_url = str(config.get("host_url", ""))
    worker_id = str(config.get("worker_id", ""))
    if not host_url or not worker_id:
        return {"host_reachable": False, "registered": False, "connected": False}
    try:
        with urllib.request.urlopen(f"{host_url}/api/agent-workers", timeout=2) as response:
            payload = json.loads(response.read())
        worker = next(
            (item for item in payload.get("workers", []) if item.get("worker_id") == worker_id),
            None,
        )
        return {
            "host_reachable": True,
            "registered": worker is not None,
            # connected 实为"已登记且未被吊销"，不代表实时在线。
            "connected": worker is not None and not worker.get("revoked", False),
            "host_worker": worker,
            "connection_error": None,
        }
    except (OSError, ValueError, urllib.error.URLError) as exc:
        return {
            "host_reachable": False,
            "registered": False,
            "connected": False,
            "host_worker": None,
            "connection_error": str(exc),
        }
