"""Storage probe: startup self-check logging and the cached health verdict."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from server.app.storage import probe
from server.app.storage.s3_settings import S3Settings

pytestmark = pytest.mark.no_db


def _settings() -> S3Settings:
    return S3Settings(bucket="materials-test", endpoint_url="http://127.0.0.1:9")


def test_probe_returns_none_when_head_bucket_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(probe, "_head_bucket", lambda settings, timeout: None)
    assert probe.probe_settings(_settings()) is None


def test_probe_returns_reason_on_failure(monkeypatch) -> None:
    def _fail(settings, timeout):
        raise ConnectionError("refused")

    monkeypatch.setattr(probe, "_head_bucket", _fail)
    reason = probe.probe_settings(_settings())
    assert reason is not None
    assert "ConnectionError" in reason


def test_startup_self_check_logs_configured_false(monkeypatch, caplog) -> None:
    monkeypatch.delenv("AGENT_LEGION_S3_BUCKET", raising=False)
    with caplog.at_level(logging.INFO, logger=probe.logger.name):
        storage = probe.build_s3_storage_checked()
    assert storage is None
    assert "materials storage: configured=false" in caplog.text


def test_startup_self_check_logs_ok(monkeypatch, caplog) -> None:
    monkeypatch.setenv("AGENT_LEGION_S3_BUCKET", "materials-test")
    monkeypatch.setattr(probe, "_head_bucket", lambda settings, timeout: None)
    with caplog.at_level(logging.INFO, logger=probe.logger.name):
        storage = probe.build_s3_storage_checked()
    assert storage is not None
    assert "materials storage: OK" in caplog.text


def test_startup_self_check_logs_degraded_without_raising(monkeypatch, caplog) -> None:
    monkeypatch.setenv("AGENT_LEGION_S3_BUCKET", "materials-test")

    def _fail(settings, timeout):
        raise TimeoutError("connect timed out")

    monkeypatch.setattr(probe, "_head_bucket", _fail)
    with caplog.at_level(logging.WARNING, logger=probe.logger.name):
        storage = probe.build_s3_storage_checked()
    # A failed probe still returns a usable client: degrade, never fail-fast.
    assert storage is not None
    assert "materials storage: DEGRADED: TimeoutError" in caplog.text


def test_health_cache_unconfigured_never_probes(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LEGION_S3_BUCKET", raising=False)

    def _forbidden(settings, timeout):
        raise AssertionError("must not probe when storage is not configured")

    monkeypatch.setattr(probe, "_head_bucket", _forbidden)
    cache = probe.StorageHealthCache(ttl_seconds=60.0)
    assert cache.status() == {"configured": False, "reachable": False}


def test_health_cache_probes_once_per_ttl(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_S3_BUCKET", "materials-test")
    calls: list[float] = []
    monkeypatch.setattr(probe, "_head_bucket", lambda s, t: calls.append(t))
    cache = probe.StorageHealthCache(ttl_seconds=60.0)
    expected = {"configured": True, "reachable": True}
    assert cache.status() == expected
    assert cache.status() == expected
    assert len(calls) == 1
    # The probe timeout passed to head_bucket stays within the 2s budget.
    assert calls[0] <= probe.PROBE_TIMEOUT_SECONDS


def test_health_cache_reprobes_after_ttl(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_LEGION_S3_BUCKET", "materials-test")
    calls: list[float] = []
    monkeypatch.setattr(probe, "_head_bucket", lambda s, t: calls.append(t))
    cache = probe.StorageHealthCache(ttl_seconds=0.0)
    cache.status()
    cache.status()
    assert len(calls) == 2


def test_cached_storage_status_reuses_app_state_cache(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LEGION_S3_BUCKET", raising=False)
    state = SimpleNamespace()
    first = probe.cached_storage_status(state)
    second = probe.cached_storage_status(state)
    assert first == second == {"configured": False, "reachable": False}
    assert isinstance(state.storage_health_cache, probe.StorageHealthCache)
