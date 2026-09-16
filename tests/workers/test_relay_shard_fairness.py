"""Focused fairness regressions for heartbeat relay shard admission (#662)."""

from __future__ import annotations

import threading
from typing import Any

import pytest

pytestmark = pytest.mark.no_db


def test_snapshot_churn_relocates_skipped_shard_by_lease_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed prefix must not turn yesterday's index into today's cursor.

    Tick 1 admits A and skips B/C. Before tick 2, A is pruned and D is
    appended. The old numeric cursor ``1`` now names C; stable identity must
    relocate B to its new index ``0`` so B flies first instead of starving
    under a continuously changing prefix.
    """
    from worker import relay_shards
    from worker.relay_thread_limiter import ShardThreadLimiter

    monkeypatch.setattr(relay_shards, "RELAY_BEAT_SHARD", 1)
    monkeypatch.setattr(relay_shards, "BATCH_BEAT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(relay_shards, "_JOIN_MARGIN_SECONDS", 0.01)

    hold = threading.Event()
    round_release = threading.Event()
    admitted: list[str] = []

    class _ParkedClient:
        def heartbeat_batch(
            self, executions: list[tuple[str, str]], timeout: float | None = None
        ) -> tuple[int, dict[str, list[str]]]:
            admitted.append(executions[0][0])
            round_release.wait(timeout=5)
            return 200, {"lost": [], "settled": [], "cancelled_execution_ids": []}

    def run_round(limiter: ShardThreadLimiter, leases: list[tuple[str, str]]) -> Any:
        round_release.clear()
        baseline = set(threading.enumerate())
        outcome = relay_shards.beat_sharded(_ParkedClient(), leases, lambda _message: None, limiter)
        round_release.set()
        for thread in threading.enumerate():
            if thread not in baseline:
                thread.join(timeout=5)
        return outcome

    def hold_slot() -> None:
        hold.wait(timeout=5)

    limiter = ShardThreadLimiter(max_inflight=2)
    held = limiter.start(hold_slot)
    assert held is not None
    try:
        first = [("A", "lease-A"), ("B", "lease-B"), ("C", "lease-C")]
        assert run_round(limiter, first).verdicts is not None
        assert admitted == ["A"]

        fresh = [("B", "lease-B"), ("C", "lease-C"), ("D", "lease-D")]
        assert run_round(limiter, fresh).verdicts is not None
        assert admitted == ["A", "B"], "the surviving skipped lease must fly first"
    finally:
        round_release.set()
        hold.set()
        held.join(timeout=5)
