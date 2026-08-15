"""低频对账（workflow_worker/catalog_reconcile.py）：热推送失败后的自愈通道。

注册/发布路径的热推送（reload_scan_entries、reload_published_executors）只在
写路径触发一次；瞬时失败后靠 poll loop 的周期对账收敛，这些用例钉住对账的
节流、漂移检测与故障隔离语义。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from server.app.workflow_worker import catalog_reconcile
from server.app.workflow_worker.catalog_reconcile import maybe_reconcile_catalogs

pytestmark = pytest.mark.no_db


def _worker(definitions: dict | None = None, reload_error: Exception | None = None):
    calls = {"reload_scan": 0}

    def _reload() -> None:
        calls["reload_scan"] += 1
        if reload_error is not None:
            raise reload_error

    worker = SimpleNamespace(
        _last_catalog_reconcile=time.monotonic(),
        reload_scan_entries=_reload,
        registry=SimpleNamespace(definitions=lambda: dict(definitions or {})),
        settings=SimpleNamespace(database_url="postgresql://unused"),
    )
    return worker, calls


def _force_due(worker) -> None:
    worker._last_catalog_reconcile = 0.0


def test_reconcile_skipped_within_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    worker, calls = _worker()
    monkeypatch.setattr(
        catalog_reconcile,
        "published_executor_definitions",
        lambda *_: pytest.fail("must not read the executor catalog within the interval"),
    )

    maybe_reconcile_catalogs(worker)

    assert calls["reload_scan"] == 0


def test_reconcile_reloads_executor_registry_on_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    worker, calls = _worker(definitions={"code-default": 1})
    _force_due(worker)
    reloads: list[int] = []
    monkeypatch.setattr(
        catalog_reconcile, "published_executor_definitions", lambda _dsn: {"code-default": 2}
    )
    monkeypatch.setattr(
        catalog_reconcile, "reload_published_executors", lambda _s, _r: reloads.append(1)
    )

    maybe_reconcile_catalogs(worker)

    assert calls["reload_scan"] == 1
    assert reloads == [1]


def test_reconcile_skips_executor_reload_when_catalog_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, calls = _worker(definitions={"code-default": 1})
    _force_due(worker)
    monkeypatch.setattr(
        catalog_reconcile, "published_executor_definitions", lambda _dsn: {"code-default": 1}
    )
    monkeypatch.setattr(
        catalog_reconcile,
        "reload_published_executors",
        lambda *_: pytest.fail("no drift: registry rebuild must be skipped"),
    )

    maybe_reconcile_catalogs(worker)

    assert calls["reload_scan"] == 1


def test_reconcile_isolates_scan_failure_from_executor_half(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker, calls = _worker(reload_error=RuntimeError("db blip"))
    _force_due(worker)
    reloads: list[int] = []
    monkeypatch.setattr(
        catalog_reconcile, "published_executor_definitions", lambda _dsn: {"code-default": 2}
    )
    monkeypatch.setattr(
        catalog_reconcile, "reload_published_executors", lambda _s, _r: reloads.append(1)
    )

    maybe_reconcile_catalogs(worker)  # must not raise

    assert calls["reload_scan"] == 1
    assert reloads == [1]


def test_reconcile_isolates_executor_read_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    worker, calls = _worker()
    _force_due(worker)

    def _raise(_dsn: str) -> dict:
        raise RuntimeError("executor catalog read failed")

    monkeypatch.setattr(catalog_reconcile, "published_executor_definitions", _raise)

    maybe_reconcile_catalogs(worker)  # must not raise

    assert calls["reload_scan"] == 1
