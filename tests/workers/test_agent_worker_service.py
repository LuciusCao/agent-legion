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
from worker.registration.token import registration_token_configured
from worker.runtime import catalog
from worker.service import create_app
from worker.service_bind import embed_control_token
from worker.supervisor import (
    WorkerConfigStore,
    WorkerSupervisor,
    public_config,
    validate_config,
)

ROOT = Path(__file__).resolve().parents[2]

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


def test_malformed_bootstrap_keeps_control_service_configurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 探测可控化：只装 velites 的机器。未配置时 disabled_runtimes 默认 []，
    # 生效声明 = 探测结果（issue #254）。
    monkeypatch.setattr(
        catalog,
        "resolve_binary",
        lambda binary: "/usr/local/bin/velites" if binary == "velites" else None,
    )
    bootstrap = tmp_path / "bootstrap.yaml"
    bootstrap.write_text("host_url: [", encoding="utf-8")

    store = WorkerConfigStore(tmp_path / "state", bootstrap)

    assert store.configured() is False
    assert store.bootstrap_error
    fallback = store.read(require_identity=False)
    assert fallback["disabled_runtimes"] == []
    assert fallback["runtimes"] == ["velites"]


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

    updated = store.update_public({}, registration_token="tok-1.host-issued-secret")
    # scoped token 落 register_tokens/ 目录，一个 token 一个 "<id>.token" 文件。
    token_path = store.state_dir / "register_tokens" / "tok-1.token"

    assert token_path.read_text(encoding="utf-8") == "tok-1.host-issued-secret\n"
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert registration_token_configured(updated, store.state_dir) is True
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
            json={"register_token": "tok-1.host-issued-secret"},
            headers=_auth(store),
        )

    assert response.status_code == 200
    assert response.json()["restarted"] is True
    assert response.json()["config"]["register_token_configured"] is True
    assert "host-issued-secret" not in response.text
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


def test_ramp_up_null_disables_and_is_hot_updated(tmp_path: Path) -> None:
    """#493 P2-1：显式 null 是唯一的 wire 禁用通道，必须落盘为禁用态。

    model_dump(exclude_none=True) 会吞掉 null——不经 fields_set 修复时，
    「PUT ramp_up: null」等价于没提交该字段，启用过爬坡的 worker 经控制台
    永远关不掉。这里钉全链路：启用（块落盘+归一化）→ 显式 null（落盘
    None、热更不重启）→ 未提交（保持禁用，partial update 语义不回退）。"""
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    supervisor = FakeSupervisor(store)
    app = create_app(supervisor, tmp_path)

    with TestClient(app) as client:
        headers = _auth(store)
        enabled = client.put(
            "/api/config",
            json={"ramp_up": {"initial": 64, "step": 64, "interval_seconds": 120}},
            headers=headers,
        )
        disabled = client.put("/api/config", json={"ramp_up": None}, headers=headers)
        untouched = client.put("/api/config", json={"max_concurrency": 5}, headers=headers)

    assert enabled.status_code == 200
    assert enabled.json()["config"]["ramp_up"] == {
        "initial": 64,
        "step": 64,
        "interval_seconds": 120,
    }
    assert enabled.json()["restarted"] is False, "ramp_up 是热更字段，不该触发重启"
    assert disabled.status_code == 200
    assert disabled.json()["config"]["ramp_up"] is None, "显式 null 必须落盘为禁用"
    assert disabled.json()["restarted"] is False
    assert untouched.status_code == 200
    assert untouched.json()["config"]["ramp_up"] is None, "未提交 ramp_up 保持现状（禁用）"
    assert supervisor.restarts == 0
    # 状态副本（executor 热读的 worker.yaml）确认为禁用态。
    assert yaml.safe_load(store.path.read_text(encoding="utf-8"))["ramp_up"] is None


def test_ramp_up_invalid_block_is_rejected_with_422(tmp_path: Path) -> None:
    """#493：非法 ramp_up 块走既有 422 fail-fast（点名字段），不落盘。"""
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={"ramp_up": {"initial": 0}},
            headers=_auth(store),
        )

    assert response.status_code == 422
    assert "ramp_up.initial" in str(response.json()["detail"])
    assert store.read()["ramp_up"] is None, "校验失败不得半应用"


def test_upload_max_concurrency_is_hot_updated_without_restart(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    supervisor = FakeSupervisor(store)
    app = create_app(supervisor, tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={"upload_max_concurrency": 12},
            headers=_auth(store),
        )

    assert response.status_code == 200
    assert response.json()["restarted"] is False
    assert response.json()["config"]["upload_max_concurrency"] == 12
    assert supervisor.restarts == 0


def test_max_code_concurrency_is_hot_updated_without_restart(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    supervisor = FakeSupervisor(store)
    app = create_app(supervisor, tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={"max_code_concurrency": 8},
            headers=_auth(store),
        )

    assert response.status_code == 200
    assert response.json()["restarted"] is False
    assert response.json()["config"]["max_code_concurrency"] == 8
    assert supervisor.restarts == 0


def test_local_api_rejects_unknown_disabled_runtime(tmp_path: Path) -> None:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={**public_config(store.read()), "disabled_runtimes": ["shell"]},
            headers=_auth(store),
        )

    assert response.status_code == 422


def test_local_api_rejects_retired_runtimes_field(tmp_path: Path) -> None:
    """旧版 opt-in `runtimes` 字段已不可编辑（issue #254）：fail-fast 422。"""
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    app = create_app(FakeSupervisor(store), tmp_path)

    with TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={"runtimes": ["pi"]},
            headers=_auth(store),
        )

    assert response.status_code == 422
    assert "runtimes" in response.text


def test_local_api_exposes_runtime_status_and_applies_disabled_runtimes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        catalog,
        "resolve_binary",
        lambda binary: f"/usr/local/bin/{binary}" if binary in {"velites", "pi"} else None,
    )
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    supervisor = FakeSupervisor(store)
    app = create_app(supervisor, tmp_path)

    with TestClient(app) as client:
        before = client.get("/api/config", headers=_auth(store))
        response = client.put(
            "/api/config",
            json={"disabled_runtimes": ["pi"]},
            headers=_auth(store),
        )

    status_rows = {row["runtime"]: row for row in before.json()["runtime_status"]}
    # _config() 的旧 runtimes: [pi] 迁移为补集停用 disabled=[velites]：
    # pi 装了且启用，velites 装了但停用。
    assert status_rows["velites"]["installed"] is True
    assert status_rows["velites"]["enabled"] is False
    assert status_rows["velites"]["binary"] == "/usr/local/bin/velites"
    assert status_rows["pi"]["installed"] is True
    assert status_rows["pi"]["enabled"] is True
    assert before.json()["runtimes"] == ["pi"]
    assert response.status_code == 200
    # disabled_runtimes 是进程级配置：改动触发重启重注册。
    assert response.json()["restarted"] is True
    assert response.json()["config"]["disabled_runtimes"] == ["pi"]
    assert response.json()["config"]["runtimes"] == ["velites"]
    assert supervisor.restarts == 1


def test_runtime_status_marks_pending_restart_against_host_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """探测/停用状态与 Host 登记集合不一致 → 标出待重启生效（#254 评审）。"""
    monkeypatch.setattr(
        catalog,
        "resolve_binary",
        lambda binary: f"/usr/local/bin/{binary}" if binary in {"velites", "pi"} else None,
    )
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config({**_config(), "disabled_runtimes": ["pi"]}))

    class RegisteredSupervisor(FakeSupervisor):
        def status(self) -> dict[str, Any]:
            # Host 仍登记着 pi 与 velites（executor 同步状态文件的内容）。
            return {**super().status(), "host_worker": {"runtimes": ["pi", "velites"]}}

    app = create_app(RegisteredSupervisor(store), tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/config", headers=_auth(store))

    payload = response.json()
    assert payload["registered_runtimes"] == ["pi", "velites"]
    rows = {row["runtime"]: row for row in payload["runtime_status"]}
    # pi 刚被停用但 Host 还登记着 → 待重启生效；velites 启用且一致 → 无 pending。
    assert rows["pi"]["enabled"] is False
    assert rows["pi"]["registered"] is True
    assert rows["pi"]["pending_restart"] is True
    assert rows["velites"]["enabled"] is True
    assert rows["velites"]["registered"] is True
    assert rows["velites"]["pending_restart"] is False


@pytest.mark.no_db
def test_validate_config_migrates_legacy_runtimes_and_preserves_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 探测可控化：两个 runtime 全部已安装。旧 opt-in 勾选迁移为补集停用，
    # 升级后 claim 行为保持不变（issue #254）。
    monkeypatch.setattr(catalog, "resolve_binary", lambda binary: f"/usr/local/bin/{binary}")
    config = validate_config({**_config(), "runtimes": ["pi", "velites"]})
    assert config["disabled_runtimes"] == []
    assert config["runtimes"] == ["pi", "velites"]
    # 旧显式声明 ["pi"] 行为保持：pi 启用，其余补集停用（迁移细节见
    # tests/workers/test_runtime_catalog.py）。
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


def _make_revoke_harness(tmp_path: Path) -> tuple[WorkerConfigStore, FakeSupervisor, Any]:
    store = WorkerConfigStore(tmp_path / "state")
    store.write(validate_config(_config()))
    supervisor = FakeSupervisor(store)
    return store, supervisor, create_app(supervisor, tmp_path)


def test_put_config_worker_id_change_logs_revoke_hint_and_restarts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, supervisor, app = _make_revoke_harness(tmp_path)

    with caplog.at_level(logging.INFO), TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={**public_config(store.read()), "worker_id": "worker-2"},
            headers=_auth(store),
        )

    assert response.status_code == 200, response.text
    assert response.json()["config"]["worker_id"] == "worker-2"
    # Host 删除注册记录是 admin-only：改 worker_id 只记提示日志，旧 worker 靠离线超时消失。
    assert any("worker-1" in record.getMessage() for record in caplog.records)
    assert supervisor.restarts == 1


def test_put_config_without_worker_id_change_logs_no_revoke_hint(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store, supervisor, app = _make_revoke_harness(tmp_path)

    with caplog.at_level(logging.INFO), TestClient(app) as client:
        response = client.put(
            "/api/config",
            json={**public_config(store.read()), "name": "Renamed Worker"},
            headers=_auth(store),
        )

    assert response.status_code == 200
    assert response.json()["config"]["name"] == "Renamed Worker"
    assert not any("旧注册记录" in record.getMessage() for record in caplog.records)
    assert supervisor.restarts == 1


def test_worker_id_change_has_no_host_revoke_channel() -> None:
    """旧实现以 register token 调 /agent-workers/{id}/revoke 必然 401（该端点
    admin-only），已随 scoped token 退役删除：service 不再持有 Host 管理客户端。"""
    assert not hasattr(service_module, "Client")


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
    """effective_host 未设置（裸机/dev 形态）＝ 现状语义：按进程 bind 判定。"""
    with caplog.at_level(logging.WARNING):
        assert embed_control_token("127.0.0.1") is True
        assert embed_control_token("::1") is True
        assert embed_control_token("localhost") is True
        assert embed_control_token("0.0.0.0") is False

    # 未传 effective 的非回环路径维持历史 warning 文案与级别（不比旧版安静）
    assert any("非回环地址 0.0.0.0" in record.message for record in caplog.records)


def test_embed_control_token_docker_loopback_publish_embeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#489 核心场景：容器内绑 0.0.0.0 + 宿主侧发布回环 → 内嵌。

    进程 bind 非回环只是端口映射前提；宿主发布 127.0.0.1 时页面仅本机
    可达，内嵌不扩大风险面。info（非 warning）级说明判定链。
    """
    with caplog.at_level(logging.INFO):
        assert embed_control_token("0.0.0.0", "127.0.0.1") is True
        assert embed_control_token("0.0.0.0", "::1") is True
        assert embed_control_token("0.0.0.0", "localhost") is True

    assert any(
        "进程绑定非回环地址 0.0.0.0，但宿主侧发布地址为回环 127.0.0.1" in record.message
        for record in caplog.records
    )


def test_embed_control_token_non_loopback_publish_blocks_embedding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """宿主侧发布非回环 → 不内嵌 + warning（真实暴露场景维持安全模型）。"""
    with caplog.at_level(logging.WARNING):
        assert embed_control_token("0.0.0.0", "0.0.0.0") is False
        assert embed_control_token("0.0.0.0", "192.168.1.5") is False
        assert embed_control_token("0.0.0.0", "192.0.2.1") is False

    # warning 消息带两个地址（进程绑定 × 宿主发布）：运维能看懂判定链
    assert any(
        "进程绑定 0.0.0.0，发布地址 192.168.1.5" in record.message for record in caplog.records
    )
    assert any("进程绑定 0.0.0.0，发布地址 0.0.0.0" in record.message for record in caplog.records)


def test_embed_control_token_loopback_process_and_publish_is_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """进程回环 + 发布回环（显式传 effective）→ 内嵌且无任何日志。"""
    with caplog.at_level(logging.INFO):
        assert embed_control_token("127.0.0.1", "127.0.0.1") is True
        assert embed_control_token("::1", "::1") is True

    assert caplog.records == []


def test_embed_control_token_loopback_process_non_loopback_publish(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """复合形态兜底：进程回环但发布面非回环 → 仍不内嵌 + warning。"""
    with caplog.at_level(logging.WARNING):
        assert embed_control_token("127.0.0.1", "0.0.0.0") is False

    assert any(
        "进程绑定 127.0.0.1，发布地址 0.0.0.0" in record.message for record in caplog.records
    )


def test_embed_control_token_bracketed_ipv6_loopback_publish(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """方括号 IPv6 回环发布（Docker ports 语法形态）→ 回环，内嵌。

    IPv6 发布必须写 AGENT_WORKER_UI_BIND=[::1]（ports 方括号语法），该值
    与 EFFECTIVE_BIND 同源传入；不剥方括号时 ip_address("[::1]") 抛
    ValueError 被判非回环——fail-closed 安全，但丢了「回环发布即内嵌」
    的判定（进程 bind 侧的方括号形态一并钉住）。
    """
    with caplog.at_level(logging.WARNING):
        assert embed_control_token("0.0.0.0", "[::1]") is True
        assert embed_control_token("[::1]") is True

    assert not any(record.levelname == logging.WARNING for record in caplog.records)


def test_embed_control_token_bracketed_ipv6_non_loopback_publish(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """方括号 IPv6 非回环（[::] 通配 / 全局地址）→ 不内嵌 + warning。"""
    with caplog.at_level(logging.WARNING):
        assert embed_control_token("0.0.0.0", "[::]") is False
        assert embed_control_token("0.0.0.0", "[2001:db8::1]") is False

    assert any("发布地址 [::]" in record.message for record in caplog.records)
    assert any("发布地址 [2001:db8::1]" in record.message for record in caplog.records)


def test_main_reads_effective_bind_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """service.main 的接线契约：effective bind 来自 AGENT_WORKER_UI_EFFECTIVE_BIND。"""
    captured: dict[str, object] = {}

    def fake_embed_control_token(host: str, effective_host: str | None = None) -> bool:
        captured["host"] = host
        captured["effective_host"] = effective_host
        return True

    def fake_create_app(supervisor: object, ui_dir: object, *, embed_token: bool) -> object:
        captured["embed_token"] = embed_token
        return object()

    monkeypatch.setattr(service_module, "embed_control_token", fake_embed_control_token)
    monkeypatch.setattr(service_module, "create_app", fake_create_app)
    monkeypatch.setattr(service_module.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(service_module.WorkerConfigStore, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(service_module.WorkerSupervisor, "__init__", lambda self, *a, **k: None)
    # pytest 的命令行参数对 argparse 不可见（裸 main() 在测试进程内执行）
    monkeypatch.setattr("sys.argv", ["worker.service"])

    # env 未设置 → effective_host=None（裸机/dev 形态，行为与现状一致）
    monkeypatch.delenv("AGENT_WORKER_UI_EFFECTIVE_BIND", raising=False)
    service_module.main()
    assert captured == {
        "host": "127.0.0.1",
        "effective_host": None,
        "embed_token": True,
    }

    # env 设置（Docker 形态，compose 注入）→ 透传给 embed_control_token
    monkeypatch.setenv("AGENT_WORKER_UI_EFFECTIVE_BIND", "127.0.0.1")
    service_module.main()
    assert captured["effective_host"] == "127.0.0.1"


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


def test_ui_assets_cover_app_js_static_imports() -> None:
    """#493 P1-1 回归钉子：ui_assets 白名单必须覆盖 app.js 的全部静态 import。

    app.js 以 ES module 顶层 import 加载本地模块（如 ./ramp_up.js），任一
    名字漏出白名单，asset() 就 404、浏览器中止整个模块图——控制台整页死
    （状态/表单/按钮全不初始化）。这里直接扫描源码求「静态 import ⊆ 白
    名单」全等：新增 import 忘记登记时立即红。白名单本身从 service 源码
    读取（不走运行时闭包，避免测试与实现共读同一变量）。"""
    import re

    app_js = (ROOT / "worker/ui/app.js").read_text(encoding="utf-8")
    imports = set(re.findall(r'from\s+"\./([A-Za-z0-9_.-]+)"\s*;', app_js))
    static_src = (ROOT / "worker/service_static.py").read_text(encoding="utf-8")
    match = re.search(r"UI_ASSETS = \(([^)]*)\)", static_src)
    assert match, "UI_ASSETS tuple not found in worker/service_static.py"
    whitelist = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert imports, "app.js 静态 import 扫描结果为空——正则或源码结构漂移"
    missing = imports - whitelist
    assert not missing, f"app.js 静态 import 未进 UI_ASSETS 白名单: {sorted(missing)}"


def test_worker_ui_serves_ramp_up_module(tmp_path: Path) -> None:
    """#493 P1-1：app.js 顶层 import 的 ./ramp_up.js 必须 200（接线级钉子）。"""
    ui = ROOT / "worker/ui"
    store = WorkerConfigStore(tmp_path / "state")
    app = create_app(FakeSupervisor(store), ui)

    with TestClient(app) as client:
        response = client.get("/assets/ramp_up.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")


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
        assert "${VELITES_PROVIDER_ENV_FILE:-./velites-provider.env}" in compose
        assert "required: false" in compose
    assert "deploy/velites-provider.env" in (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "COPY shared /app/shared" in dockerfile
    assert 'python3 -c "import worker.service' in dockerfile
    assert "worker/cli_args.py /usr/local/bin/agent_worker_cli_args.py" in dockerfile


def test_compose_files_publish_effective_bind_to_worker_service() -> None:
    """#489：容器内必绑 0.0.0.0，宿主侧发布地址须同步传给 service。

    AGENT_WORKER_UI_EFFECTIVE_BIND 与 ports 发布行同一 ${AGENT_WORKER_UI_BIND}
    插值源（.env 一处改、两处同步），service 据此判定 token 是否内嵌。
    三个 compose 的 worker 服务都要带——standalone 是一键安装的拉取目标，
    漏一处即该形态退化回「永不内嵌」的旧判定（读文件断言钉住双/三文件
    同步的先例见 test_compose_files_carry_velites_mount_and_guard）。
    """
    for name in (
        "deploy/compose.worker.yaml",
        "deploy/compose.worker.standalone.yaml",
        "deploy/compose.host.yaml",
    ):
        compose = (ROOT / name).read_text(encoding="utf-8")
        # 与 ports 发布行同源：同一变量、同一默认值，用户改 .env 一处生效两处
        assert "AGENT_WORKER_UI_EFFECTIVE_BIND: ${AGENT_WORKER_UI_BIND:-127.0.0.1}" in compose, (
            f"{name} 缺宿主侧发布地址注入或插值与 ports 行不同源"
        )
        assert "${AGENT_WORKER_UI_BIND:-127.0.0.1}" in compose, f"{name} 缺 UI bind 发布插值行"


def test_compose_host_worker_network_isolated_from_peer_services() -> None:
    """#489 P1 网络契约：host compose 的 worker 不得与 postgres/seaweedfs/rustfs 共网。

    worker 控制台 GET / 无鉴权（token 内嵌页面的设计前提），同网 peer 容器
    即可 curl http://worker:8787/ 提取 control token 接管控制面——默认回环
    发布下 service 会内嵌 token（判定只看宿主侧发布面），挡不住 compose
    内网。断言以解析 YAML 求网络集合交集的方式钉住（字符串包含式断言钉不
    住「共享隐式 default」的缺省形态——不写 networks 键时五个服务在文本上
    完全一致）。
    """
    doc = yaml.safe_load((ROOT / "deploy/compose.host.yaml").read_text(encoding="utf-8"))
    services = doc["services"]

    def service_networks(name: str) -> set[str]:
        # compose 语义：服务未声明 networks 键时挂隐式 default 网络
        raw = services[name].get("networks") or ["default"]
        return set(raw) if isinstance(raw, list) else set(raw)

    worker_networks = service_networks("worker")
    assert worker_networks, "worker 未声明 networks：隐式 default 会让它与全部 peer 共网"
    for peer in ("postgres", "seaweedfs", "rustfs"):
        shared = worker_networks & service_networks(peer)
        assert not shared, (
            f"compose.host.yaml 的 worker 与 {peer} 共享网络 {sorted(shared)}：peer 容器"
            "可达无鉴权的 worker 控制台，默认回环发布下的 token 内嵌即向同网段"
            "泄漏 control token（issue #489 安全不变量）"
        )
    # worker 出站依赖的唯一 peer 是 host：隔离不得切断 host_url 通道
    assert worker_networks & service_networks("host"), (
        "worker 与 host 无共享网络：register/claim/heartbeat/result 将全部失联"
    )
    # host 双挂 default：postgres / 对象存储的既有依赖不受隔离影响
    assert "default" in service_networks("host")


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
