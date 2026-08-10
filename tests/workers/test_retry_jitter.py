"""Full-jitter backoff tests for worker/_retry.py."""

from __future__ import annotations

import pytest

from worker import _retry
from worker._retry import run_with_retry

pytestmark = pytest.mark.no_db


def _always_fail(calls: dict[str, int]) -> None:
    calls["n"] += 1
    raise RuntimeError("boom")


def test_backoff_waits_are_jittered_within_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    calls = {"n": 0}

    with pytest.raises(RuntimeError, match="boom"):
        run_with_retry(
            lambda: _always_fail(calls),
            retriable=(RuntimeError,),
            base_seconds=1.0,
            max_attempts=4,
        )

    # 3 次退避，每次落在 [0, backoff]，上限仍按 2x 推进（1, 2, 4）。
    assert len(sleeps) == 3
    assert all(0 <= sleep <= cap for sleep, cap in zip(sleeps, [1.0, 2.0, 4.0], strict=True))


def test_jitter_uses_random_uniform_and_preserves_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[float, float]] = []
    monkeypatch.setattr(_retry.random, "uniform", lambda lo, hi: seen.append((lo, hi)) or hi)
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    calls = {"n": 0}

    with pytest.raises(RuntimeError):
        run_with_retry(
            lambda: _always_fail(calls),
            retriable=(RuntimeError,),
            base_seconds=2.0,
            cap_seconds=4.0,
            max_attempts=4,
        )

    # 确定性上限按 2x 推进并被 cap 封顶；jitter 只作用在实际等待上。
    assert seen == [(0, 2.0), (0, 4.0), (0, 4.0)]
    assert sleeps == [2.0, 4.0, 4.0]


def test_on_retry_receives_jittered_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_retry.random, "uniform", lambda lo, hi: hi / 2)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    delays: list[float] = []
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    result = run_with_retry(
        flaky,
        retriable=(RuntimeError,),
        base_seconds=2.0,
        on_retry=lambda _exc, delay: delays.append(delay),
    )

    assert result == "ok"
    assert delays == [1.0, 2.0]
