"""Claim-success pacing tests (worker/claim_pacing.py, issue #472).

The old claim loop waited a fixed 0.2s after a successful pass — with
#448 phase 1 shrinking the round-trip to tens of milliseconds, that
fixed wait became ~3/4 of the loop period and pinned the claim rate at
``1 / (0.2s + round-trip)``. The new pacing maps the observed round-trip
to a wait of half its length, clamped into [10ms, 100ms], reported on
change through the log callable. These tests pin:

- the band mapping (floor / ceiling / ratio) as a pure function;
- the three loop paths' division of labor: success adapts, empty queue
  resets to floor (the loop itself still waits poll_interval), errors
  stay with ClaimBackoffSequence (asserted here as "no pacing change
  while the error path owns the wait");
- the log line format (``worker claim pacing <ms>ms``, change-only).
"""

from __future__ import annotations

import pytest

from worker.claim_backoff import CLAIM_BACKOFF_FIRST_SECONDS, ClaimBackoffSequence
from worker.claim_pacing import (
    CLAIM_PACING_CEIL_SECONDS,
    CLAIM_PACING_FLOOR_SECONDS,
    CLAIM_PACING_TARGET_RATIO,
    LEGACY_CLAIM_PACING_SECONDS,
    ClaimPacing,
    clamp_claim_pacing,
)

pytestmark = pytest.mark.no_db


def test_floor_at_fast_round_trips() -> None:
    # 快往返（phase 1 后的几十毫秒）打到下沿：10ms。
    assert clamp_claim_pacing(0.0) == CLAIM_PACING_FLOOR_SECONDS
    assert clamp_claim_pacing(0.02) == CLAIM_PACING_FLOOR_SECONDS
    assert clamp_claim_pacing(CLAIM_PACING_FLOOR_SECONDS / CLAIM_PACING_TARGET_RATIO) == (
        CLAIM_PACING_FLOOR_SECONDS
    )


def test_ceil_at_slow_round_trips() -> None:
    # 慢往返（回到秒级）钳在上沿：100ms，不随往返继续放大。
    assert clamp_claim_pacing(1.0) == CLAIM_PACING_CEIL_SECONDS
    assert clamp_claim_pacing(30.0) == CLAIM_PACING_CEIL_SECONDS


def test_band_interpolation_is_half_the_round_trip() -> None:
    # 带内 = 往返 × 0.5（比事务自身节奏慢半拍，去相关于往返抖动）。
    for round_trip in (0.04, 0.1, 0.19):
        expected = round_trip * CLAIM_PACING_TARGET_RATIO
        assert CLAIM_PACING_FLOOR_SECONDS < expected < CLAIM_PACING_CEIL_SECONDS
        assert clamp_claim_pacing(round_trip) == expected


def test_negative_round_trip_clamps_to_floor() -> None:
    # monotonic 时钟理论上可返回相等戳；防御性输入仍钳在下沿。
    assert clamp_claim_pacing(-1.0) == CLAIM_PACING_FLOOR_SECONDS


def test_pacing_constants_pin_the_design() -> None:
    # 契约钉子：10ms 下沿（claim 写事务的争用护栏）、100ms 上沿、0.5 比例。
    assert CLAIM_PACING_FLOOR_SECONDS == 0.01
    assert CLAIM_PACING_CEIL_SECONDS == 0.1
    assert CLAIM_PACING_TARGET_RATIO == 0.5
    # 上沿必须显著低于被替换的固定 0.2s，否则退回固定间隔天花板。
    assert CLAIM_PACING_CEIL_SECONDS < LEGACY_CLAIM_PACING_SECONDS


def test_success_path_adapts_and_tracks_round_trips() -> None:
    pacing = ClaimPacing(log=None)
    # 首次成功（快往返）→ 下沿；往返变慢 → 等待按比例抬升并钳在上沿。
    assert pacing.next_wait(0.01) == CLAIM_PACING_FLOOR_SECONDS
    assert pacing.next_wait(0.06) == pytest.approx(0.03)
    assert pacing.next_wait(0.5) == CLAIM_PACING_CEIL_SECONDS
    # 回到快往返立即回落——无迟滞，pacing 是往返的即时函数。
    assert pacing.next_wait(0.02) == CLAIM_PACING_FLOOR_SECONDS


def test_untimed_success_pass_keeps_current_pacing() -> None:
    # 无法计时的成功 pass：维持当前等待（不误报变化、不抬不降）。
    pacing = ClaimPacing(log=None)
    assert pacing.next_wait(0.06) == pytest.approx(0.03)
    assert pacing.next_wait(None) == pytest.approx(0.03)
    assert pacing.current_wait == pytest.approx(0.03)


def test_empty_queue_path_resets_to_floor() -> None:
    # 空队列路径：pacing 归位下沿（循环自身仍等 poll_interval——那是
    # executor 的接线）；下一次成功重新爬坡。
    pacing = ClaimPacing(log=None)
    pacing.next_wait(0.5)  # 上沿
    pacing.reset()
    assert pacing.current_wait == CLAIM_PACING_FLOOR_SECONDS
    assert pacing.next_wait(0.06) == pytest.approx(0.03)


def test_wait_after_pass_success_returns_adaptive_wait() -> None:
    # executor 主循环的单点接线：成功 pass 返回自适应等待（= next_wait）。
    lines: list[str] = []
    pacing = ClaimPacing(log=lines.append)
    assert pacing.wait_after_pass(True, 0.06, 2.0) == pytest.approx(0.03)
    assert pacing.wait_after_pass(True, 0.5, 2.0) == CLAIM_PACING_CEIL_SECONDS
    assert lines == ["worker claim pacing 30ms", "worker claim pacing 100ms"]


def test_wait_after_pass_empty_returns_poll_interval_and_resets() -> None:
    # 空队列 pass：返回调用方传入的 poll_interval（语义归 executor 所有），
    # 且 pacing 归位下沿——硬约束「空队列路径行为不变」的模块侧钉子。
    lines: list[str] = []
    pacing = ClaimPacing(log=lines.append)
    pacing.next_wait(0.5)  # 上沿
    assert pacing.wait_after_pass(False, 0.0, 2.0) == 2.0
    assert pacing.current_wait == CLAIM_PACING_FLOOR_SECONDS
    # 归位产生一条日志（当前生效等待值的变化可观测）。
    assert lines == ["worker claim pacing 100ms", "worker claim pacing 10ms"]


def test_error_path_pacing_untouched_and_backoff_owns_the_wait() -> None:
    # 错误路径分工：executor 在 except 臂直接走 ClaimBackoffSequence，
    # 不触碰 pacing——这里钉住两件事：错误等待由 backoff 序列给出
    # （#437 序列原样），且 pacing 状态在错误风暴期间保持冻结。
    backoff = ClaimBackoffSequence(rng=lambda: 0.5)
    waits = [backoff.next_wait() for _ in range(4)]
    assert waits[0] == CLAIM_BACKOFF_FIRST_SECONDS
    assert waits[1:] == [1.0, 2.0, 4.0]

    pacing = ClaimPacing(log=None)
    pacing.next_wait(0.5)  # 上沿
    # 模拟错误臂：只推进 backoff，不调 pacing
    for _ in range(3):
        backoff.next_wait()
    assert pacing.current_wait == CLAIM_PACING_CEIL_SECONDS
    # 恢复后（backoff.reset + 成功 pass）pacing 立即回到自适应轨道。
    backoff.reset()
    assert pacing.next_wait(0.02) == CLAIM_PACING_FLOOR_SECONDS


def test_initial_pacing_is_the_floor() -> None:
    pacing = ClaimPacing(log=None)
    assert pacing.current_wait == CLAIM_PACING_FLOOR_SECONDS


def test_log_reports_changes_only_and_matches_worker_log_style() -> None:
    lines: list[str] = []
    pacing = ClaimPacing(log=lines.append)
    pacing.next_wait(0.01)  # floor（首条：从未生效值变化而来）
    pacing.next_wait(0.01)  # 无变化 → 不重复
    pacing.next_wait(0.06)  # 30ms
    pacing.next_wait(0.5)  # 上沿
    pacing.reset()  # 归位下沿
    assert lines == [
        "worker claim pacing 10ms",
        "worker claim pacing 30ms",
        "worker claim pacing 100ms",
        "worker claim pacing 10ms",
    ]


def test_untimed_success_pass_emits_no_log_line() -> None:
    lines: list[str] = []
    pacing = ClaimPacing(log=lines.append)
    pacing.next_wait(None)
    assert lines == []


def test_custom_floor_and_ceil_override() -> None:
    pacing = ClaimPacing(floor_seconds=0.02, ceil_seconds=0.08, log=None)
    assert pacing.next_wait(0.0) == 0.02
    assert pacing.next_wait(0.3) == 0.08
    assert pacing.next_wait(0.1) == pytest.approx(0.05)
