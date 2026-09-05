"""Claim backoff sequence tests (worker/claim_backoff.py, issue #437).

The old claim loop waited ``poll_interval`` on the first failure and doubled
with no jitter: a fleet-wide Host blip aligned every Worker's retry, and the
fixed first wait cost a full poll interval. The new sequence is a fixed
short first delay (1s), then exponential doubling with ±20% jitter, capped
at 60s. The jitter source is injectable, so the sequence is asserted
deterministically at the band edges.
"""

from __future__ import annotations

import pytest

from worker.claim_backoff import (
    CLAIM_BACKOFF_CAP_SECONDS,
    CLAIM_BACKOFF_FIRST_SECONDS,
    CLAIM_BACKOFF_JITTER,
    ClaimBackoffSequence,
    jittered_claim_backoff,
)

pytestmark = pytest.mark.no_db


def test_first_failure_is_a_short_fixed_delay() -> None:
    # 首次失败固定 1s，与 poll_interval 无关。
    assert jittered_claim_backoff(0, rng=lambda: 0.0) == CLAIM_BACKOFF_FIRST_SECONDS
    assert jittered_claim_backoff(0, rng=lambda: 1.0) == CLAIM_BACKOFF_FIRST_SECONDS


def test_deterministic_progression_doubles_from_first() -> None:
    # 中位 jitter（draw=0.5）时序列恰为 1, 2, 4, 8, 16, 32, 60（cap 封顶）。
    mid = ClaimBackoffSequence(rng=lambda: 0.5)
    assert [mid.next_wait() for _ in range(7)] == [1.0, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0]
    assert mid.next_wait() == CLAIM_BACKOFF_CAP_SECONDS
    assert mid.next_wait() == CLAIM_BACKOFF_CAP_SECONDS


def test_jitter_band_is_twenty_percent() -> None:
    # draw=0 → 下沿 0.8x；draw=1 → 上沿 1.2x。cap 处上沿被钳回 cap。
    low = ClaimBackoffSequence(rng=lambda: 0.0)
    high = ClaimBackoffSequence(rng=lambda: 1.0)
    low_waits = [low.next_wait() for _ in range(7)]
    high_waits = [high.next_wait() for _ in range(7)]
    assert low_waits == [1.0, 0.8, 1.6, 3.2, 6.4, 12.8, 25.6]
    assert high_waits == [1.0, 1.2, 2.4, 4.8, 9.6, 19.2, 38.4]
    # 序列继续推进到 cap 区：确定性值恒为 cap，下沿 jitter 钳在 0.8*cap=48，
    # 上沿钳回 cap —— jitter 永不超过 cap，也永不停在 cap 之上。
    assert low.next_wait() == 48.0
    assert high.next_wait() == CLAIM_BACKOFF_CAP_SECONDS
    assert low.next_wait() == 48.0
    assert high.next_wait() == CLAIM_BACKOFF_CAP_SECONDS


def test_jitter_never_exceeds_cap() -> None:
    # 确定性值已到 cap 时，上沿 jitter 也被钳制在 cap。
    assert jittered_claim_backoff(20, rng=lambda: 1.0) == CLAIM_BACKOFF_CAP_SECONDS


def test_reset_restores_first_delay() -> None:
    seq = ClaimBackoffSequence(rng=lambda: 0.5)
    for _ in range(4):
        seq.next_wait()
    seq.reset()
    assert seq.next_wait() == CLAIM_BACKOFF_FIRST_SECONDS


def test_custom_first_and_cap_override() -> None:
    seq = ClaimBackoffSequence(first_seconds=2.0, cap_seconds=6.0, jitter=0.0, rng=lambda: 0.5)
    assert [seq.next_wait() for _ in range(4)] == [2.0, 2.0, 4.0, 6.0]


def test_jitter_constant_is_twenty_percent() -> None:
    # 契约钉子：±20% 是 issue #437 的选定带宽。
    assert CLAIM_BACKOFF_JITTER == 0.2
    assert CLAIM_BACKOFF_CAP_SECONDS == 60.0
