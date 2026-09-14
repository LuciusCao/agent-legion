"""Worker 控制台 token 内嵌判定测试（#489）。

从 test_agent_worker_service.py 拆出（1108 行超 tests 1000 上限）：token
内嵌/embed 的判定矩阵与接线契约是一个独立关注面——「页面 token 是否随
宿主侧发布面内嵌」的判定逻辑在 worker/service_bind.py，本文件钉住其
全部矩阵分支与 service.main 的 env 接线；index 页注入/占位符保留两个
端到端用例（经 create_app 的 embed_token 参数）。共享件（FakeSupervisor
等）不跨文件 import（tests/app/test_pytest_postgres_boundaries.py 的
守卫），这里的用例只依赖模块级函数与 create_app。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import worker.service as service_module
from worker.service import create_app
from worker.service_bind import embed_control_token
from worker.supervisor import WorkerConfigStore


class _FakeSupervisor:
    def __init__(self, store: WorkerConfigStore) -> None:
        self.store = store

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def restart(self) -> None:
        pass

    def status(self) -> dict[str, object]:
        return {"service": "running"}


def test_index_injects_control_token(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text(
        '<script>window.__WORKER_CONTROL_TOKEN__ = "__WORKER_CONTROL_TOKEN__";</script>',
        encoding="utf-8",
    )
    store = WorkerConfigStore(tmp_path / "state")
    app = create_app(_FakeSupervisor(store), ui)

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
    app = create_app(_FakeSupervisor(store), ui, embed_token=False)

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
