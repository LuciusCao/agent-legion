"""#738 per-token request bucket math: burst, Retry-After, window recovery.

Pure in-memory logic with an injected clock — no database, no HTTP.
"""

from __future__ import annotations

import pytest

from server.app.auth.api_token_limits import (
    DEFAULT_BURST,
    DEFAULT_REQUESTS_PER_MINUTE,
    ApiTokenLimits,
    InMemoryApiTokenLimiter,
    limits_from_config,
)
from server.app.configuration.env_overrides import apply_env_overrides

pytestmark = pytest.mark.no_db


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter(clock: _Clock, rpm: int, burst: int) -> InMemoryApiTokenLimiter:
    return InMemoryApiTokenLimiter(
        ApiTokenLimits(requests_per_minute=rpm, burst=burst), monotonic=lambda: clock.now
    )


def test_defaults_and_config_wiring(monkeypatch) -> None:
    assert limits_from_config({}) == ApiTokenLimits(DEFAULT_REQUESTS_PER_MINUTE, DEFAULT_BURST)
    assert (DEFAULT_REQUESTS_PER_MINUTE, DEFAULT_BURST) == (60, 20)
    assert InMemoryApiTokenLimiter().limits == ApiTokenLimits()
    # The env-only auth section carries the instance-wide knobs.
    monkeypatch.setenv("AGENT_LEGION_API_TOKEN_RATE_LIMIT_PER_MINUTE", "120")
    monkeypatch.setenv("AGENT_LEGION_API_TOKEN_RATE_LIMIT_BURST", "5")
    config: dict = {}
    apply_env_overrides(config)
    assert limits_from_config(config) == ApiTokenLimits(requests_per_minute=120, burst=5)


@pytest.mark.parametrize(
    "auth", [{"api_token_rate_limit_per_minute": 0}, {"api_token_rate_limit_burst": -1}]
)
def test_invalid_config_fails_fast(auth: dict) -> None:
    with pytest.raises(ValueError):
        limits_from_config({"auth": auth})


def test_bucket_allows_burst_then_refuses_with_retry_after() -> None:
    clock = _Clock()
    limiter = _limiter(clock, rpm=60, burst=3)
    for _ in range(3):
        assert limiter.acquire_request("t").allowed
    refused = limiter.acquire_request("t")
    assert not refused.allowed
    # 60/min = 1 token/s, bucket is empty -> one whole second to the next.
    assert refused.retry_after_seconds == 1


def test_retry_after_rounds_up_for_slow_rates() -> None:
    clock = _Clock()
    # 6/min = one token every 10 s.
    limiter = _limiter(clock, rpm=6, burst=1)
    assert limiter.acquire_request("t").allowed
    assert limiter.acquire_request("t").retry_after_seconds == 10
    clock.advance(3.5)
    # 6.5 s of deficit left -> rounded UP to 7 (a retry at 6 would fail).
    assert limiter.acquire_request("t").retry_after_seconds == 7


def test_window_recovery_after_retry_after_elapses() -> None:
    clock = _Clock()
    limiter = _limiter(clock, rpm=6, burst=2)
    assert limiter.acquire_request("t").allowed
    assert limiter.acquire_request("t").allowed
    refused = limiter.acquire_request("t")
    assert not refused.allowed
    clock.advance(refused.retry_after_seconds)
    assert limiter.acquire_request("t").allowed
    # Long idle refills to the burst capacity, never beyond it.
    clock.advance(3600)
    assert limiter.acquire_request("t").allowed
    assert limiter.acquire_request("t").allowed
    assert not limiter.acquire_request("t").allowed


def test_buckets_are_per_token() -> None:
    clock = _Clock()
    limiter = _limiter(clock, rpm=1, burst=1)
    assert limiter.acquire_request("noisy").allowed
    assert not limiter.acquire_request("noisy").allowed
    # A sibling token's budget is untouched by the noisy neighbour.
    assert limiter.acquire_request("quiet").allowed
