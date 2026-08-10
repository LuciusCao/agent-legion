"""Pure-function tests for the graduated claim backpressure gate
(worker/transfer_controls.py claim_availability)."""

from __future__ import annotations

import pytest

from worker.transfer_controls import claim_availability

pytestmark = pytest.mark.no_db


def test_default_hard_limit_is_double_capacity() -> None:
    # capacity=8 → hard=16，soft=8。
    assert claim_availability(5, 0, 8, None) == 5
    assert claim_availability(5, 8, 8, None) == 5
    assert claim_availability(5, 16, 8, None) == 0
    assert claim_availability(5, 100, 8, None) == 0


def test_explicit_backlog_limit() -> None:
    # hard=4，soft=2。
    assert claim_availability(6, 0, 8, 4) == 6
    assert claim_availability(6, 2, 8, 4) == 6
    assert claim_availability(6, 4, 8, 4) == 0
    assert claim_availability(6, 5, 8, 4) == 0


def test_linear_decay_between_soft_and_hard() -> None:
    # hard=16，soft=8：depth=12 → 8*(16-12)//(16-8) = 4。
    assert claim_availability(8, 10, 8, None) == 6
    assert claim_availability(8, 12, 8, None) == 4
    assert claim_availability(8, 14, 8, None) == 2


def test_degenerate_single_slot_backlog_keeps_hard_gate() -> None:
    # hard=1 → soft=0：退化为旧硬门，depth>=1 即归零。
    assert claim_availability(6, 0, 8, 1) == 6
    assert claim_availability(6, 1, 8, 1) == 0
    assert claim_availability(6, 2, 8, 1) == 0


def test_zero_base_stays_zero() -> None:
    assert claim_availability(0, 0, 8, None) == 0
    assert claim_availability(0, 9, 8, None) == 0


def test_availability_monotonic_non_increasing_in_depth() -> None:
    values = [claim_availability(8, depth, 8, None) for depth in range(20)]
    assert all(first >= second for first, second in zip(values, values[1:], strict=False))
