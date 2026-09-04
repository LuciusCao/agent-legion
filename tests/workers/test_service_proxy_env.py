"""worker.service 入口代理环境剥离测试（#441）。

启动 shell 继承的代理 env 会让 velites（reqwest 默认读代理 env）把全部
LLM 流量绕经本机代理进程；代理配置重载/订阅刷新会整批掐断在途流。
service.main() 在派生任何子进程之前调用 strip_proxy_env() 兜底。
"""

from __future__ import annotations

import pytest

from worker import service, service_env

pytestmark = pytest.mark.no_db

_ALL_PROXY_VARS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
)


def test_strip_proxy_env_removes_all_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALL_PROXY_VARS:
        monkeypatch.setenv(name, "http://127.0.0.1:7897")

    service_env.strip_proxy_env()

    import os

    for name in _ALL_PROXY_VARS:
        assert name not in os.environ


def test_strip_proxy_env_keeps_unrelated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("LLM_GATEWAY_TOKEN", "secret")

    service_env.strip_proxy_env()

    import os

    assert "https_proxy" not in os.environ
    assert os.environ["PATH"] == "/usr/bin"
    assert os.environ["LLM_GATEWAY_TOKEN"] == "secret"


def test_strip_proxy_env_keep_flag_preserves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")
    monkeypatch.setenv("WORKER_KEEP_PROXY_ENV", "1")

    service_env.strip_proxy_env()

    import os

    assert os.environ["https_proxy"] == "http://127.0.0.1:7897"


def test_strip_proxy_env_noop_without_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ALL_PROXY_VARS:
        monkeypatch.delenv(name, raising=False)

    # 无代理 env 时是幂等 no-op，不抛错。
    service_env.strip_proxy_env()


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
