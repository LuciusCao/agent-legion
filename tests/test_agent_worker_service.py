from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

import scripts.agent_worker_service_state as state_module
from scripts.agent_worker_service import create_app
from scripts.agent_worker_service_state import (
    WorkerConfigStore,
    WorkerSupervisor,
    public_config,
    validate_config,
)

ROOT = Path(__file__).resolve().parents[1]

FAKE_WORKER = """
import os, sys, time
print("fake worker ready", flush=True)
mode = os.environ.get("FAKE_WORKER_MODE", "sleep")
if mode == "sleep":
    time.sleep(30)
sys.exit(2 if mode == "exit2" else 1)
"""


def _config() -> dict[str, Any]:
    return {
        "host_url": "http://host.test:8000/",
        "worker_id": "worker-1",
        "name": "Test Worker",
        "runtimes": ["pi"],
        "max_concurrency": 3,
        "labels": {"arch": "arm64"},
        "register_token_file": "/run/secrets/register-token",
        "work_root": "/tmp/worker-executions",
        "poll_interval_seconds": 2,
        "heartbeat_interval_seconds": 15,
        "shutdown_grace_seconds": 25,
        "environment": {"PRESERVED": "yes"},
    }


def _auth(store: WorkerConfigStore) -> dict[str, str]:
    return {"Authorization": f"Bearer {store.control_token()}"}


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


class FakeSupervisor:
    def __init__(self, store: WorkerConfigStore) -> None:
        self.store = store
        self.restarts = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def restart(self) -> None:
        self.restarts += 1

    def status(self) -> dict[str, Any]:
        return {
            "service": "running",
            "configured": self.store.configured(),
            "worker_running": True,
            "host_reachable": True,
            "registered": True,
            "connected": True,
        }

    def logs(self, limit: int = 200) -> list[str]:
        return ["registered", "waiting"][-limit:]


def _make_supervisor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> WorkerSupervisor:
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    token_file = tmp_path / "register-token"
    token_file.write_text("secret", encoding="utf-8")
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config({**_config(), "register_token_file": str(token_file)}))
    monkeypatch.setattr(state_module, "_query_remote_status", lambda config: {})
    monkeypatch.setattr(state_module, "_RESTART_BACKOFF_INITIAL", 0.05)
    monkeypatch.setenv("FAKE_WORKER_MODE", mode)
    return WorkerSupervisor(store, script)


def test_config_store_bootstraps_yaml_and_writes_managed_copy(tmp_path: Path) -> None:
    bootstrap = tmp_path / "bootstrap.yaml"
    bootstrap.write_text(yaml.safe_dump(_config()), encoding="utf-8")
    store = WorkerConfigStore(tmp_path / "state", bootstrap)

    assert store.read()["host_url"] == "http://host.test:8000"
    assert store.path.is_file()
    assert public_config(store.read())["max_concurrency"] == 3


def test_malformed_bootstrap_keeps_control_service_configurable(tmp_path: Path) -> None:
    bootstrap = tmp_path / "bootstrap.yaml"
    bootstrap.write_text("host_url: [", encoding="utf-8")

    store = WorkerConfigStore(tmp_path / "state", bootstrap)

    assert store.configured() is False
    assert store.bootstrap_error
    assert store.read(require_identity=False)["runtimes"] == ["pi"]


def test_public_update_preserves_secret_paths_and_environment(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))

    updated = store.update_public({**public_config(store.read()), "max_concurrency": 8})

    assert updated["max_concurrency"] == 8
    assert updated["register_token_file"] == "/run/secrets/register-token"
    assert updated["environment"] == {"PRESERVED": "yes"}


def test_concurrent_public_updates_all_succeed_and_leave_readable_state(
    tmp_path: Path,
) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: store.update_public({"max_concurrency": n}), range(1, 17)))

    final = store.read()
    assert 1 <= final["max_concurrency"] <= 16
    assert final["worker_id"] == "worker-1"


def test_validate_config_rejects_credentials_in_host_url() -> None:
    config = {**_config(), "host_url": "http://user:password@host.test:8000"}

    try:
        validate_config(config)
    except ValueError as exc:
        assert "Host 地址" in str(exc)
    else:
        raise AssertionError("credential-bearing host URL should fail")


def test_local_api_returns_status_and_applies_configuration(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    supervisor = FakeSupervisor(store)
    app = create_app(supervisor, tmp_path)

    with TestClient(app) as client:
        headers = _auth(store)
        status = client.get("/api/status", headers=headers)
        response = client.put(
            "/api/config",
            json={**public_config(store.read()), "max_concurrency": 6},
            headers=headers,
        )
        logs = client.get("/api/logs?limit=1", headers=headers)

    assert status.json()["connected"] is True
    assert response.status_code == 200
    assert response.json()["config"]["max_concurrency"] == 6
    assert "register_token_file" not in response.json()["config"]
    assert "environment" not in response.json()["config"]
    assert supervisor.restarts == 1
    assert logs.json() == {"lines": ["waiting"]}


def test_local_api_rejects_unknown_runtime(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={**public_config(store.read()), "runtimes": ["shell"]},
            headers=_auth(store),
        )

    assert response.status_code == 422


def test_local_api_partial_update_keeps_unspecified_fields(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)

    with TestClient(app) as client:
        response = client.put("/api/config", json={"max_concurrency": 9}, headers=_auth(store))

    assert response.status_code == 200
    config = response.json()["config"]
    assert config["max_concurrency"] == 9
    assert config["worker_id"] == "worker-1"
    assert config["host_url"] == "http://host.test:8000"


def test_api_requires_bearer_token_except_health(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)

    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/logs").status_code == 401
        assert client.post("/api/restart").status_code == 401
        wrong = {"Authorization": "Bearer wrong-token"}
        assert client.get("/api/status", headers=wrong).status_code == 401
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/status", headers=_auth(store)).status_code == 200


def test_index_injects_control_token(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text(
        '<script>window.__WORKER_CONTROL_TOKEN__ = "__WORKER_CONTROL_TOKEN__";</script>',
        encoding="utf-8",
    )
    store = WorkerConfigStore(tmp_path / "state")
    app = create_app(FakeSupervisor(store), ui)

    with TestClient(app) as client:
        body = client.get("/").text

    assert f'= "{store.control_token()}"' in body
    assert '= "__WORKER_CONTROL_TOKEN__"' not in body


def test_supervisor_starts_and_stops_worker_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _make_supervisor(tmp_path, monkeypatch, "sleep")

    supervisor.start()
    _wait_for(lambda: supervisor.running())
    _wait_for(lambda: any("fake worker ready" in line for line in supervisor.logs()))
    pid = supervisor.status()["pid"]
    assert isinstance(pid, int)

    supervisor.stop()
    _wait_for(lambda: not supervisor.running())
    time.sleep(0.2)
    assert supervisor.running() is False  # 手动停止后不自动重启


def test_supervisor_restarts_after_crash_with_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supervisor = _make_supervisor(tmp_path, monkeypatch, "crash")
    try:
        supervisor.start()
        _wait_for(lambda: supervisor.status()["restart_count"] >= 1)
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
    _wait_for(lambda: supervisor.status()["failed"] is not None)

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
    _wait_for(lambda: supervisor.running())

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


def test_compose_keeps_control_api_local_and_state_separate_from_executions() -> None:
    standalone = (ROOT / "deploy/compose.worker.yaml").read_text(encoding="utf-8")
    host = (ROOT / "deploy/compose.host.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    for compose in (standalone, host):
        assert "${AGENT_WORKER_UI_BIND:-127.0.0.1}:8787:8787" in compose
        assert "worker-control:/var/lib/agent-legion-worker-control" in compose
        assert "worker-data:/var/lib/agent-legion-worker" in compose
    assert "server/app/workflows/pi_protocol.py" in dockerfile
