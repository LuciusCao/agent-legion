from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

import worker.service as service_module
import worker.supervisor as state_module
from tests.helpers import wait_for_predicate
from worker.metrics_cache import WorkerMetricsCache, metrics_cache_key, metrics_cache_path
from worker.registration_token import registration_token_configured
from worker.service import create_app
from worker.service_bind import embed_control_token
from worker.supervisor import (
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


def _auth(store: WorkerConfigStore) -> dict[str, str]:
    return {"Authorization": f"Bearer {store.control_token()}"}


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
            "max_concurrency": 3,
            "upload_max_concurrency": 4,
            "running_executions_count": 0,
            "upload_queued_count": 0,
            "upload_active_count": 0,
            "current_executions": [],
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
    assert store.read(require_identity=False)["runtimes"] == ["velites"]


def test_unconfigured_worker_defaults_to_claim_disabled(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")

    assert store.read(require_identity=False)["claim_enabled"] is False


def test_public_update_preserves_secret_paths_and_environment(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))

    updated = store.update_public({**public_config(store.read()), "max_concurrency": 8})

    assert updated["max_concurrency"] == 8
    assert updated["register_token_file"] == "/run/secrets/register-token"
    assert updated["environment"] == {"PRESERVED": "yes"}


def test_registration_token_is_write_only_and_stored_with_private_permissions(
    tmp_path: Path,
) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))

    updated = store.update_public({}, registration_token="host-issued-token")
    token_path = Path(updated["register_token_file"])

    assert token_path.read_text(encoding="utf-8") == "host-issued-token\n"
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert registration_token_configured(updated) is True
    assert "register_token" not in public_config(updated)
    assert "register_token_file" not in public_config(updated)


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
    assert response.json()["restarted"] is False
    assert supervisor.restarts == 0
    assert logs.json() == {"lines": ["waiting"]}


def test_local_api_stores_registration_token_without_returning_it(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    supervisor = FakeSupervisor(store)
    app = create_app(supervisor, tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={"register_token": "host-issued-token"},
            headers=_auth(store),
        )

    assert response.status_code == 200
    assert response.json()["restarted"] is True
    assert response.json()["config"]["register_token_configured"] is True
    assert "host-issued-token" not in response.text
    assert supervisor.restarts == 1


def test_claim_switch_and_capacity_are_hot_updated_without_restart(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    supervisor = FakeSupervisor(store)
    app = create_app(supervisor, tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={"claim_enabled": False, "max_concurrency": 9},
            headers=_auth(store),
        )

    assert response.status_code == 200
    assert response.json()["restarted"] is False
    assert response.json()["config"]["claim_enabled"] is False
    assert response.json()["config"]["max_concurrency"] == 9
    assert supervisor.restarts == 0


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


@pytest.mark.no_db
def test_validate_config_accepts_pi_and_preserves_explicit_runtimes() -> None:
    config = validate_config({**_config(), "runtimes": ["pi", "velites"]})
    assert config["runtimes"] == ["pi", "velites"]
    # 显式声明 ["pi"] 保持原样：pi 仍是合法 runtime，只是不再是默认值
    # （默认值见 test_malformed_bootstrap_keeps_control_service_configurable）。
    assert validate_config(_config())["runtimes"] == ["pi"]


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


def _make_revoke_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[WorkerConfigStore, FakeSupervisor, Any, list[tuple[str, str, str]]]:
    token_file = tmp_path / "register-token"
    token_file.write_text("management-token\n", encoding="utf-8")
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config({**_config(), "register_token_file": str(token_file)}))
    supervisor = FakeSupervisor(store)
    calls: list[tuple[str, str, str]] = []

    class FakeHostClient:
        def __init__(self, host: str) -> None:
            self.host = host

        def revoke(self, worker_id: str, management_token: str) -> None:
            calls.append((self.host, worker_id, management_token))

    monkeypatch.setattr(service_module, "Client", FakeHostClient)
    return store, supervisor, create_app(supervisor, tmp_path), calls


def test_put_config_revokes_previous_worker_id_before_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, supervisor, app, calls = _make_revoke_harness(tmp_path, monkeypatch)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={**public_config(store.read()), "worker_id": "worker-2"},
            headers=_auth(store),
        )

    assert response.status_code == 200, response.text
    assert response.json()["config"]["worker_id"] == "worker-2"
    # 旧 Host 地址 + 旧 worker_id + register_token_file 里的 management token。
    assert calls == [("http://host.test:8000", "worker-1", "management-token")]
    assert supervisor.restarts == 1


def test_put_config_without_worker_id_change_does_not_revoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, supervisor, app, calls = _make_revoke_harness(tmp_path, monkeypatch)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={**public_config(store.read()), "name": "Renamed Worker"},
            headers=_auth(store),
        )

    assert response.status_code == 200
    assert response.json()["config"]["name"] == "Renamed Worker"
    assert calls == []
    assert supervisor.restarts == 1


def test_put_config_revoke_failure_still_saves_and_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, supervisor, app, calls = _make_revoke_harness(tmp_path, monkeypatch)

    def failing_revoke(self: Any, worker_id: str, management_token: str) -> None:
        calls.append((self.host, worker_id, management_token))
        raise RuntimeError("connection refused")

    monkeypatch.setattr(service_module.Client, "revoke", failing_revoke)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={**public_config(store.read()), "worker_id": "worker-2"},
            headers=_auth(store),
        )

    assert response.status_code == 200, response.text
    assert response.json()["config"]["worker_id"] == "worker-2"
    assert calls == [("http://host.test:8000", "worker-1", "management-token")]
    assert supervisor.restarts == 1


def test_api_requires_bearer_token_except_health(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)

    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get("/api/logs").status_code == 401
        assert client.post("/api/restart").status_code == 401
        assert client.get("/api/metrics/overview").status_code == 401
        wrong = {"Authorization": "Bearer wrong-token"}
        assert client.get("/api/status", headers=wrong).status_code == 401
        assert client.get("/api/metrics/overview", headers=wrong).status_code == 401
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/status", headers=_auth(store)).status_code == 200


def test_metrics_overview_validates_query_params(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)

    with TestClient(app) as client:
        headers = _auth(store)
        assert (
            client.get("/api/metrics/overview?granularity=second", headers=headers).status_code
            == 422
        )


def test_metrics_overview_reads_worker_authenticated_cache(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)
    payload = {
        "granularity": "24h",
        "buckets": [
            {
                "bucket_start": "2026-07-26T12:00:00+00:00",
                "online_workers": 2,
                "online_workers_max": 3,
                "active_executions": 1,
                "active_executions_max": 2,
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 10,
                "total_tokens": 160,
            }
        ],
    }
    WorkerMetricsCache(metrics_cache_path(store.state_dir)).publish(
        {metrics_cache_key("24h"): payload}
    )

    with TestClient(app) as client:
        response = client.get("/api/metrics/overview?granularity=24h", headers=_auth(store))

    assert response.status_code == 200
    assert response.json() == payload


def test_metrics_overview_without_cache_returns_503(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/metrics/overview", headers=_auth(store))

    assert response.status_code == 503
    assert "等待 Worker" in response.json()["detail"]


def test_metrics_overview_cache_error_returns_503(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)
    WorkerMetricsCache(metrics_cache_path(store.state_dir)).publish({}, "6h: connection refused")

    with TestClient(app) as client:
        response = client.get("/api/metrics/overview", headers=_auth(store))

    assert response.status_code == 503
    assert "connection refused" in response.json()["detail"]


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


def test_index_skips_control_token_when_embedding_disabled(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text(
        '<script>window.__WORKER_CONTROL_TOKEN__ = "__WORKER_CONTROL_TOKEN__";</script>',
        encoding="utf-8",
    )
    store = WorkerConfigStore(tmp_path / "state")
    app = create_app(FakeSupervisor(store), ui, embed_token=False)

    with TestClient(app) as client:
        body = client.get("/").text

    assert store.control_token() not in body
    assert '= "__WORKER_CONTROL_TOKEN__"' in body


def test_embed_control_token_only_on_loopback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert embed_control_token("127.0.0.1") is True
        assert embed_control_token("::1") is True
        assert embed_control_token("localhost") is True
        assert embed_control_token("0.0.0.0") is False

    assert any("非回环地址 0.0.0.0" in record.message for record in caplog.records)


def test_worker_ui_serves_icon_sprite(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<div>worker</div>", encoding="utf-8")
    (ui / "icons.svg").write_text("<svg></svg>", encoding="utf-8")
    store = WorkerConfigStore(tmp_path / "state")
    app = create_app(FakeSupervisor(store), ui)

    with TestClient(app) as client:
        response = client.get("/assets/icons.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["Cache-Control"] == "no-cache"


def test_index_disables_browser_caching(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<div>worker</div>", encoding="utf-8")
    store = WorkerConfigStore(tmp_path / "state")
    app = create_app(FakeSupervisor(store), ui)

    with TestClient(app) as client:
        response = client.get("/")

    assert response.headers["Cache-Control"] == "no-cache"


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


def test_compose_keeps_control_api_local_and_state_separate_from_executions() -> None:
    standalone = (ROOT / "deploy/compose.worker.yaml").read_text(encoding="utf-8")
    host = (ROOT / "deploy/compose.host.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    for compose in (standalone, host):
        assert "${AGENT_WORKER_UI_BIND:-127.0.0.1}:8787:8787" in compose
        assert "worker-control:/var/lib/agent-legion-worker-control" in compose
        assert "worker-data:/var/lib/agent-legion-worker" in compose
    assert "COPY shared /app/shared" in dockerfile
    assert 'python3 -c "import worker.service' in dockerfile
    assert "worker/cli_args.py /usr/local/bin/agent_worker_cli_args.py" in dockerfile


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


def test_status_endpoint_exposes_current_executions(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/status", headers=_auth(store))
    assert response.status_code == 200
    assert response.json()["current_executions"] == []


def test_status_endpoint_breaks_out_running_and_upload_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "fake_worker.py"
    script.write_text(
        """
import json, os, time
path = os.environ["AGENT_WORKER_STATUS_FILE"]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({
        "pid": os.getpid(),
        "remote": {"host_reachable": True, "registered": True, "connected": True, "host_worker": {"worker_id": "worker-1"}, "connection_error": None},
        "executions": {
            "exec-1": {"execution_id": "exec-1", "node_key": "node_a", "phase": "running", "started_at": "2026-07-23T00:00:00+00:00"},
            "exec-2": {"execution_id": "exec-2", "node_key": "node_b", "phase": "downloading", "started_at": "2026-07-23T00:00:00+00:00"},
            "exec-3": {"execution_id": "exec-3", "node_key": "node_c", "phase": "queued_upload", "started_at": "2026-07-23T00:00:00+00:00"},
            "exec-4": {"execution_id": "exec-4", "node_key": "node_d", "phase": "uploading", "started_at": "2026-07-23T00:00:00+00:00"},
        },
    }, handle)
time.sleep(30)
""",
        encoding="utf-8",
    )
    token_file = tmp_path / "register-token"
    token_file.write_text("secret", encoding="utf-8")
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config({**_config(), "register_token_file": str(token_file)}))
    supervisor = WorkerSupervisor(store, script)
    supervisor.start()
    try:
        wait_for_predicate(lambda: supervisor.status()["current_executions"] != [])
        status = supervisor.status()
        assert status["running_executions_count"] == 2
        assert status["upload_queued_count"] == 1
        assert status["upload_active_count"] == 1
        assert status["upload_max_concurrency"] == 4
    finally:
        supervisor.stop()
