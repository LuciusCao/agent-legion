"""Worker supervisor 进程生命周期测试（启动/停止、崩溃重启退避、退出码 2、
restart/stop 竞态、状态文件注入与清理）。从 test_agent_worker_service.py
按主题拆出（原文件超 800 行，用例零改动迁移）；共享件按文件内副本维护
（tests/app/test_pytest_postgres_boundaries.py 的守卫不允许跨文件 import）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import worker.supervisor as state_module
from tests.helpers import wait_for_predicate
from worker.supervisor import WorkerConfigStore, WorkerSupervisor, validate_config

FAKE_WORKER = """
import os, sys, time
print("fake worker ready", flush=True)
mode = os.environ.get("FAKE_WORKER_MODE", "sleep")
if mode == "sleep":
    time.sleep(30)
sys.exit(2 if mode == "exit2" else 1)
"""

FAKE_WORKER_WITH_STATUS = """
import json, os, time
path = os.environ["AGENT_WORKER_STATUS_FILE"]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({"pid": os.getpid(), "remote": {"host_reachable": True, "registered": True, "connected": True, "host_worker": {"worker_id": "worker-1", "name": "Test Worker"}, "connection_error": None}, "executions": {"exec-1": {"execution_id": "exec-1", "job_id": "job-1", "node_key": "node_a", "phase": "running", "started_at": "2026-07-23T00:00:00+00:00"}}}, handle)
time.sleep(30)
"""


def _config() -> dict[str, Any]:
    return {
        "host_url": "http://host.test:8000/",
        "worker_id": "worker-1",
        "name": "Test Worker",
        "runtimes": ["pi"],
        "max_concurrency": 3,
        "upload_max_concurrency": 4,
        "labels": {"arch": "arm64"},
        "register_token_file": "/run/secrets/register-token",
        "work_root": "/tmp/worker-executions",
        "poll_interval_seconds": 2,
        "heartbeat_interval_seconds": 15,
        "shutdown_grace_seconds": 25,
        "environment": {"PRESERVED": "yes"},
    }


def _make_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> WorkerSupervisor:
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    token_file = tmp_path / "register-token"
    token_file.write_text("secret", encoding="utf-8")
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config({**_config(), "register_token_file": str(token_file)}))
    monkeypatch.setattr(state_module, "_RESTART_BACKOFF_INITIAL", 0.05)
    monkeypatch.setenv("FAKE_WORKER_MODE", mode)
    return WorkerSupervisor(store, script)


def test_supervisor_starts_and_stops_worker_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _make_supervisor(tmp_path, monkeypatch, "sleep")
    supervisor.store.update_public({"claim_enabled": True})

    supervisor.start()
    wait_for_predicate(lambda: supervisor.running())
    wait_for_predicate(lambda: any("fake worker ready" in line for line in supervisor.logs()))
    pid = supervisor.status()["pid"]
    assert isinstance(pid, int)
    assert supervisor.status()["claim_enabled"] is False
    supervisor.store.update_public({"claim_enabled": True})
    supervisor.restart()
    wait_for_predicate(lambda: supervisor.running())
    assert supervisor.status()["claim_enabled"] is False

    supervisor.stop()
    wait_for_predicate(lambda: not supervisor.running())
    # 保留：负向观察窗——手动 stop 后「不」自动重启。
    time.sleep(0.2)
    assert supervisor.running() is False  # 手动停止后不自动重启


def test_supervisor_restarts_after_crash_with_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _make_supervisor(tmp_path, monkeypatch, "crash")
    try:
        supervisor.start()
        wait_for_predicate(lambda: supervisor.status()["restart_count"] >= 1)
        status = supervisor.status()
        assert status["failed"] is None
        assert status["next_restart_delay"] is not None or status["worker_running"]
    finally:
        supervisor.stop()


def test_supervisor_does_not_restart_after_exit_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _make_supervisor(tmp_path, monkeypatch, "exit2")

    supervisor.start()
    wait_for_predicate(lambda: supervisor.status()["failed"] is not None)

    # 保留：负向观察窗——退出码 2 后「不」重启。
    time.sleep(0.3)
    status = supervisor.status()
    assert "退出码 2" in status["failed"]
    assert status["exit_code"] == 2
    assert status["restart_count"] == 0
    assert status["worker_running"] is False


def test_supervisor_restart_and_stop_can_race_without_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _make_supervisor(tmp_path, monkeypatch, "sleep")
    supervisor.start()
    wait_for_predicate(lambda: supervisor.running())

    errors: list[BaseException] = []

    def run(action: Callable[[], None]) -> None:
        try:
            action()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=(supervisor.restart,)),
        threading.Thread(target=run, args=(supervisor.stop,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    supervisor.stop()
    assert supervisor.running() is False


def test_supervisor_injects_status_file_and_cleans_it_on_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER_WITH_STATUS, encoding="utf-8")
    token_file = tmp_path / "register-token"
    token_file.write_text("secret", encoding="utf-8")
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config({**_config(), "register_token_file": str(token_file)}))
    supervisor = WorkerSupervisor(store, script)
    metrics_path = tmp_path / "state" / "ops_metrics.json"
    metrics_path.write_text("stale", encoding="utf-8")
    supervisor.start()
    try:
        assert not metrics_path.exists()
        wait_for_predicate(lambda: supervisor.status()["current_executions"] != [])
        metrics_path.write_text("runtime", encoding="utf-8")
        status = supervisor.status()
        executions = status["current_executions"]
        assert [item["execution_id"] for item in executions] == ["exec-1"]
        assert executions[0]["phase"] == "running"
        assert status["host_reachable"] is True
        assert status["registered"] is True
        assert status["host_worker"]["worker_id"] == "worker-1"
    finally:
        supervisor.stop()
    wait_for_predicate(lambda: supervisor.status()["current_executions"] == [])
    assert not (tmp_path / "state" / "current_executions.json").exists()
    assert not metrics_path.exists()
