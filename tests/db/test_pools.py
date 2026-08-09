"""Pool recycling knobs: max_idle / max_lifetime are explicit and env-tunable."""

from __future__ import annotations

import pytest

from server.app.db import pools

pytestmark = pytest.mark.no_db


@pytest.fixture
def captured_pool(monkeypatch):
    captured: dict[str, object] = {}

    class FakePool:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __class_getitem__(cls, item):
            return cls

        def close(self):
            pass

    monkeypatch.setattr(pools, "ConnectionPool", FakePool)
    with pools._POOLS_LOCK:
        pools._POOLS.clear()
    yield captured
    with pools._POOLS_LOCK:
        pools._POOLS.clear()


def _dsn(suffix: str) -> str:
    return f"postgresql://u:p@localhost/db_{suffix}"


def test_pool_uses_tighter_recycling_defaults(captured_pool) -> None:
    pools.pool_for(_dsn("defaults"))
    assert captured_pool["max_idle"] == 120.0
    assert captured_pool["max_lifetime"] == 900.0


def test_pool_recycling_env_overrides(captured_pool, monkeypatch) -> None:
    monkeypatch.setenv(pools._POOL_MAX_IDLE_ENV, "30")
    monkeypatch.setenv(pools._POOL_MAX_LIFETIME_ENV, "120.5")
    pools.pool_for(_dsn("overrides"))
    assert captured_pool["max_idle"] == 30.0
    assert captured_pool["max_lifetime"] == 120.5


def test_pool_recycling_invalid_env_falls_back(captured_pool, monkeypatch) -> None:
    monkeypatch.setenv(pools._POOL_MAX_IDLE_ENV, "not-a-number")
    monkeypatch.setenv(pools._POOL_MAX_LIFETIME_ENV, "")
    pools.pool_for(_dsn("invalid"))
    assert captured_pool["max_idle"] == 120.0
    assert captured_pool["max_lifetime"] == 900.0
