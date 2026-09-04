"""worker 出网代理治理测试（#444）。

启动 shell 继承的代理 env 会让 velites（reqwest 默认读代理 env）与
executor 的 requests 客户端把全部出网流量绕经本机代理进程；代理配置
重载/订阅刷新会整批掐断在途流。service.main() 在派生任何子进程之前
剥离代理 env（默认直连），确需代理出口的部署在 worker.yaml 显式配置
proxy 字段，supervisor 派生 executor 时按配置注入。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

import worker.supervisor as supervisor_module
from worker import service, service_env
from worker.config_store import validate_config
from worker.proxy_config import validate_proxy
from worker.service_env import PROXY_ENV_VARS, proxy_env_overrides
from worker.supervisor import WorkerConfigStore, WorkerSupervisor

pytestmark = pytest.mark.no_db


def test_strip_proxy_env_removes_all_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROXY_ENV_VARS:
        monkeypatch.setenv(name, "http://127.0.0.1:7897")

    service_env.strip_proxy_env()

    for name in PROXY_ENV_VARS:
        assert name not in os.environ


def test_strip_proxy_env_is_idempotent_without_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROXY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    service_env.strip_proxy_env()


def test_validate_proxy_accepts_http_https_socks_and_empty() -> None:
    assert validate_proxy("") == ""
    assert validate_proxy(None) == ""
    assert validate_proxy("  http://127.0.0.1:7897 ") == "http://127.0.0.1:7897"
    assert validate_proxy("http://user:pass@gateway:8080") == "http://user:pass@gateway:8080"
    assert validate_proxy("socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    assert validate_proxy("socks5h://gateway:1080") == "socks5h://gateway:1080"


@pytest.mark.parametrize(
    "bad",
    ["not a url", "ftp://proxy:21", "http://p:1/?x=1", "http://p:1/#frag", "://missing-scheme"],
)
def test_validate_proxy_rejects_non_http_schemes_and_extras(bad: str) -> None:
    with pytest.raises(ValueError, match="proxy"):
        validate_proxy(bad)


def test_proxy_env_overrides_fill_all_variants() -> None:
    overrides = proxy_env_overrides("http://gateway:8080")
    assert overrides == dict.fromkeys(PROXY_ENV_VARS, "http://gateway:8080")


def test_proxy_env_overrides_empty_config_means_no_injection() -> None:
    assert proxy_env_overrides("") == {}
    assert proxy_env_overrides(None) == {}


def test_validate_config_persists_proxy_field() -> None:
    config = validate_config(
        {"host_url": "http://host:8000", "worker_id": "w1", "proxy": "http://gateway:8080"}
    )
    assert config["proxy"] == "http://gateway:8080"
    assert validate_config({**config, "proxy": ""})["proxy"] == ""


def test_validate_config_rejects_bad_proxy() -> None:
    with pytest.raises(ValueError, match="proxy"):
        validate_config({"host_url": "http://host:8000", "worker_id": "w1", "proxy": "junk"})


def test_main_invokes_strip_before_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() 必须在构造 WorkerSupervisor（会派生 executor）之前剥离代理。"""
    order: list[str] = []

    class _FakeStore:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            order.append("store")

    class _FakeSupervisor:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            order.append("supervisor")

    monkeypatch.setattr(service, "WorkerConfigStore", _FakeStore)
    monkeypatch.setattr(service, "WorkerSupervisor", _FakeSupervisor)
    monkeypatch.setattr(service, "create_app", lambda *a, **k: None)
    monkeypatch.setattr(service.uvicorn, "run", lambda *a, **k: order.append("uvicorn"))
    monkeypatch.setattr(service, "strip_proxy_env", lambda: order.append("strip"))
    monkeypatch.setattr(
        "sys.argv",
        ["worker.service", "--state-dir", "/tmp/w-test-state"],
    )

    service.main()

    assert order[0] == "strip"
    assert "supervisor" in order
    assert order[-1] == "uvicorn"


class _FakePopen:
    def __init__(self, argv: list[str], **kwargs: object) -> None:
        self.argv = argv
        self.env = kwargs.get("env")
        self.returncode = None

    def poll(self) -> None:
        return None


def _proxy_store(tmp_path: Path, proxy: str) -> WorkerConfigStore:
    store = WorkerConfigStore(tmp_path / "state")
    token = tmp_path / "register-token"
    token.write_text("secret", encoding="utf-8")
    store.write(
        validate_config(
            {
                "host_url": "http://host.test:8000/",
                "worker_id": "worker-1",
                "max_concurrency": 1,
                "register_token_file": str(token),
                "proxy": proxy,
            }
        )
    )
    return store


def test_supervisor_injects_configured_proxy_into_executor_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置了 proxy：派生 executor 的 env 里全部代理变量都指向该 URL。"""
    captured: dict[str, Any] = {}

    class _RecordingPopen(_FakePopen):
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            captured["env"] = kwargs.get("env")
            super().__init__(argv, **kwargs)

    # 生产路径语义：service 入口已剥离代理 env，supervisor 看到的是干净环境；
    # 测试 shell 本机可能挂着代理，先清掉再断言。
    for name in PROXY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(supervisor_module.subprocess, "Popen", _RecordingPopen)
    monkeypatch.setattr(WorkerSupervisor, "_reap_orphans", lambda self: None)
    monkeypatch.setattr(WorkerSupervisor, "_collect_logs", lambda self, *a: None)
    store = _proxy_store(tmp_path, "http://gateway:8080")
    supervisor = WorkerSupervisor(store, tmp_path / "worker.py")

    supervisor._start()

    env = captured["env"]
    assert env is not None
    assert {name: env[name] for name in PROXY_ENV_VARS} == dict.fromkeys(
        PROXY_ENV_VARS, "http://gateway:8080"
    )


def test_put_config_proxy_change_triggers_restart(tmp_path: Path) -> None:
    """proxy 是进程级配置：PUT 修改后走重启路径，而不是热更新。"""
    from fastapi.testclient import TestClient

    from worker.service import create_app

    class _FakeSupervisor:
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
            return {"configured": True}

        def logs(self, limit: int = 200) -> list[str]:
            return []

        def token_status(self) -> dict[str, str]:
            return {}

    store = _proxy_store(tmp_path, "")
    app = create_app(_FakeSupervisor(store), tmp_path, embed_token=False)
    headers = {"Authorization": f"Bearer {store.control_token()}"}

    with TestClient(app) as client:
        response = client.put("/api/config", json={"proxy": "http://gateway:8080"}, headers=headers)

    assert response.status_code == 200
    assert response.json()["restarted"] is True
    assert response.json()["config"]["proxy"] == "http://gateway:8080"
    assert store.read()["proxy"] == "http://gateway:8080"


def test_supervisor_default_keeps_stripped_env_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未配置 proxy：派生 env 不含任何代理变量（入口剥离未被注入回填）。"""
    captured: dict[str, Any] = {}

    class _RecordingPopen(_FakePopen):
        def __init__(self, argv: list[str], **kwargs: object) -> None:
            captured["env"] = kwargs.get("env")
            super().__init__(argv, **kwargs)

    for name in PROXY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(supervisor_module.subprocess, "Popen", _RecordingPopen)
    monkeypatch.setattr(WorkerSupervisor, "_reap_orphans", lambda self: None)
    monkeypatch.setattr(WorkerSupervisor, "_collect_logs", lambda self, *a: None)
    store = _proxy_store(tmp_path, "")
    supervisor = WorkerSupervisor(store, tmp_path / "worker.py")

    supervisor._start()

    env = captured["env"]
    assert env is not None
    for name in PROXY_ENV_VARS:
        assert name not in env
