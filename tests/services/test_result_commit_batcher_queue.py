"""Unit tests for the result-commit batcher's in-memory queue semantics.

Split from ``test_result_commit_batcher.py`` once that integration suite
crossed the repository's 800-line proactive split threshold.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import server.app.agent_broker.result_commit_batcher as _batcher_module
from server.app.agent_broker.result_commit_batcher import (
    MAX_ITEMS_PER_TRANSACTION,
    ResultCommitBatcher,
)

pytestmark = pytest.mark.no_db


class _CountingArm:
    """Batched-arm double: counts calls, returns per-item verdicts."""

    def __init__(self, verdicts: list | None = None, fail_on: set[int] | None = None) -> None:
        self.calls: list[list[tuple]] = []
        self.verdicts = verdicts
        self.fail_on = fail_on or set()

    def __call__(self, args: list[tuple]):
        self.calls.append(list(args))
        if self.fail_on and len(self.calls) - 1 in self.fail_on:
            raise RuntimeError("boom")
        if self.verdicts is not None:
            return list(self.verdicts)
        return [True] * len(args)


def _wait_for_depth(batcher: ResultCommitBatcher, depth: int) -> None:
    deadline = time.monotonic() + 5
    while batcher._queue.qsize() < depth and time.monotonic() < deadline:
        time.sleep(0.01)
    assert batcher._queue.qsize() >= depth, f"queue never reached depth {depth}"


def test_submit_returns_verdict_through_queue() -> None:
    arm = _CountingArm()
    batcher = ResultCommitBatcher(arm, arm)
    batcher.start()
    try:
        assert batcher.submit("finish", ("lease-1",)) is True
        assert batcher.submit("mark_done", ("exec-1",)) is True
    finally:
        batcher.stop()
    assert [call for call in arm.calls if call] == [[("lease-1",)], [("exec-1",)]]


def test_wave_batches_into_one_transaction_per_kind() -> None:
    calls: list[list[tuple]] = []
    entered = threading.Event()
    release = threading.Event()

    def _recording_arm(args: list[tuple]):
        calls.append(list(args))
        if len(calls) == 1:
            entered.set()
            release.wait(timeout=5)
        return [True] * len(args)

    batcher = ResultCommitBatcher(_recording_arm, _recording_arm)
    batcher.start()
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            first = pool.submit(batcher.submit, "finish", ("lease-1",))
            assert entered.wait(timeout=5)
            rest = [pool.submit(batcher.submit, "finish", (f"lease-{i}",)) for i in range(2, 6)]
            _wait_for_depth(batcher, 4)
            release.set()
            assert first.result(timeout=5) is True
            assert all(future.result(timeout=5) is True for future in rest)
    finally:
        release.set()
        batcher.stop()
    assert calls == [[("lease-1",)], [(f"lease-{i}",) for i in range(2, 6)]]


def test_isolation_fallback_reruns_items_individually() -> None:
    def _flaky(args: list[tuple]):
        if len(args) > 1:
            raise RuntimeError("slice aborted")
        if args[0][0] == "lease-bad":
            raise RuntimeError("deterministic failure")
        return [True]

    batcher = ResultCommitBatcher(_flaky, _flaky)
    batcher.start()
    try:
        assert batcher.submit("finish", ("lease-1",)) is True
        with pytest.raises(RuntimeError, match="deterministic failure"):
            batcher.submit("finish", ("lease-bad",))
    finally:
        batcher.stop()


def test_stop_drains_parked_items() -> None:
    """Release a gated writer while stop joins it, then verify exit-drain."""
    arm_entered = threading.Event()
    release_arm = threading.Event()
    arm_calls: list[list[tuple]] = []

    def _gated_arm(args: list[tuple]):
        arm_calls.append(list(args))
        if len(arm_calls) == 1:
            arm_entered.set()
            release_arm.wait(timeout=5)
        return [True] * len(args)

    batcher = ResultCommitBatcher(_gated_arm, _gated_arm)
    batcher.start()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(batcher.submit, "finish", ("lease-1",))
            assert arm_entered.wait(timeout=5)
            parked = [pool.submit(batcher.submit, "finish", (f"lease-{i}",)) for i in range(2, 4)]
            _wait_for_depth(batcher, 2)
            stopping = pool.submit(batcher.stop)
            _wait_for_depth(batcher, 3)  # two items plus stop's sentinel
            release_arm.set()
            stopping.result(timeout=5)
            assert first.result(timeout=5) is True
            assert all(future.result(timeout=5) is True for future in parked)
    finally:
        release_arm.set()
        batcher.stop()
    flat = [args for call in arm_calls for args in call]
    assert sorted(flat) == sorted((f"lease-{i}",) for i in range(1, 4))


def test_stop_timeout_is_explicit_while_writer_is_still_draining() -> None:
    """A finite stop timeout must not report success before the arm exits."""
    entered = threading.Event()
    release = threading.Event()

    def _blocked_arm(args):  # noqa: ANN001
        entered.set()
        release.wait(timeout=5)
        return [True] * len(args)

    batcher = ResultCommitBatcher(_blocked_arm, _blocked_arm)
    batcher.start()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(batcher.submit, "finish", ("lease-1",))
        assert entered.wait(timeout=5)
        with pytest.raises(TimeoutError, match="still draining"):
            batcher.stop(timeout_seconds=0.01)
        release.set()
        assert future.result(timeout=5) is True
    batcher.stop(timeout_seconds=1)


def test_max_items_bound_splits_rounds() -> None:
    """Pre-fill 2× the cap so the writer must drain exactly two rounds."""
    arm = _CountingArm()
    batcher = ResultCommitBatcher(arm, arm)
    queued = [
        _batcher_module._BatchItem(kind="finish", args=(f"lease-{i}",))
        for i in range(2 * MAX_ITEMS_PER_TRANSACTION)
    ]
    for item in queued:
        batcher._queue.put(item)

    batcher.start()
    try:
        assert all(item.future.done.wait(timeout=5) for item in queued)
        assert all(item.future.result is True for item in queued)
    finally:
        batcher.stop()
    assert [len(call) for call in arm.calls] == [
        MAX_ITEMS_PER_TRANSACTION,
        MAX_ITEMS_PER_TRANSACTION,
    ]


def test_restart_reopens_the_queue_after_stop() -> None:
    arm = _CountingArm()
    batcher = ResultCommitBatcher(arm, arm)
    batcher.stop()
    batcher.start()
    try:
        assert batcher.submit("finish", ("lease-1",)) is True
    finally:
        batcher.stop()
    assert arm.calls == [[("lease-1",)]]

    arm2 = _CountingArm()
    batcher.finish_many = arm2
    batcher.mark_done_many = arm2
    batcher.start()
    batcher.stop()
    batcher.start()
    try:
        assert batcher.submit("mark_done", ("exec-1",)) is True
    finally:
        batcher.stop()
    assert arm2.calls == [[("exec-1",)]]
