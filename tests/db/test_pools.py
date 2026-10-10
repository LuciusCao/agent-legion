"""Pool recycling knobs: max_idle / max_lifetime are explicit and env-tunable."""

from __future__ import annotations

import pytest

from server.app.db import pool_sizing, pools

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
    monkeypatch.setenv(pool_sizing.POOL_MAX_IDLE_ENV, "30")
    monkeypatch.setenv(pool_sizing.POOL_MAX_LIFETIME_ENV, "120.5")
    pools.pool_for(_dsn("overrides"))
    assert captured_pool["max_idle"] == 30.0
    assert captured_pool["max_lifetime"] == 120.5


def test_pool_recycling_invalid_env_falls_back(captured_pool, monkeypatch) -> None:
    monkeypatch.setenv(pool_sizing.POOL_MAX_IDLE_ENV, "not-a-number")
    monkeypatch.setenv(pool_sizing.POOL_MAX_LIFETIME_ENV, "")
    pools.pool_for(_dsn("invalid"))
    assert captured_pool["max_idle"] == 120.0
    assert captured_pool["max_lifetime"] == 900.0


class _RecordingPool:
    def __init__(self, name: str, log: list[tuple[str, str]]) -> None:
        self._name = name
        self._log = log

    def close(self) -> None:
        self._log.append(("close", self._name))


@pytest.mark.parametrize("entry", ["close", "settle"])
def test_close_and_settle_only_settle_this_process_pools(monkeypatch, entry) -> None:
    """#1077: close_database_pools is the single close chokepoint (lifespan
    shutdown and test boundaries) — every own pool is settled before its
    close; a pool inherited across fork is never settled (no workers in
    this process) but is still closed by close. settle never closes."""
    import os

    log: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pools, "settle_pool_returns", lambda pool: log.append(("settle", pool._name))
    )
    own, inherited = _RecordingPool("own", log), _RecordingPool("inherited", log)
    # Park the session's live pools aside (and restore them) instead of
    # dropping them: a cleared real pool would leak its workers/connections.
    with pools._POOLS_LOCK:
        saved = dict(pools._POOLS)
        pools._POOLS.clear()
        pools._POOLS[(os.getpid(), _dsn("own"))] = own
        pools._POOLS[(os.getpid() + 1, _dsn("inherited"))] = inherited
    try:
        if entry == "close":
            pools.close_database_pools()
            assert log == [("settle", "own"), ("close", "own"), ("close", "inherited")]
            assert pools._POOLS == {}
        else:
            pools.settle_database_pools()
            assert log == [("settle", "own")]
            assert len(pools._POOLS) == 2
    finally:
        with pools._POOLS_LOCK:
            pools._POOLS.clear()
            pools._POOLS.update(saved)


def test_lifespan_closes_through_the_settling_chokepoint() -> None:
    """main.py's lifespan must reach the settling close, not a bypass.

    Static check (importing the app module would make this a postgres-tier
    file): main.py takes ``close_database_pools`` from db.connection, which
    re-exports the pools chokepoint itself.
    """
    from pathlib import Path

    from server.app.db import connection

    main_source = (Path(__file__).resolve().parents[2] / "server/app/main.py").read_text()
    assert "from server.app.db.connection import close_database_pools\n" in main_source
    assert connection.close_database_pools is pools.close_database_pools
