"""Claim-success pacing for the Worker supervisor (issue #472).

After a successful claim pass the old loop always waited a fixed 0.2s:
with #448 phase 1 bringing the claim round-trip down to tens of
milliseconds, that fixed wait became the dominant term of the loop
period (roughly three quarters of it), pinning the claim rate at
``1 / (0.2s + round-trip)`` — a math-level ceiling the fleet rides
against even while capacity is free and claims never fail.

This module replaces the fixed wait with an adaptive short pacing that
scales with the claim round-trip actually observed, bounded to a narrow
band:
- **Floor** (``CLAIM_PACING_FLOOR_SECONDS`` = 10ms): never zero. A claim
  is a write transaction taking advisory locks and running the promote
  write sequence; an unthrottled client can re-enter that sequence
  instantly and amplify contention on the very locks #437's deadlock
  sawtooth taught us to respect. The floor keeps a small breath between
  transactions.
- **Ceiling** (``CLAIM_PACING_CEIL_SECONDS`` = 100ms): a "success-path"
  wait materially above this would reintroduce a fixed-interval
  ceiling. The band keeps the effective claim rate between roughly
  ``1/(floor + rt)`` and ``1/(ceil + rt)`` for any observed round-trip.
- **Target ratio** (``CLAIM_PACING_TARGET_RATIO`` = 0.5): pace at half
  the last successful claim round-trip. Faster than the transaction
  itself buys nothing (the Host is the serial resource), and a sub-1
  ratio de-correlates the pacing from the round-trip's own jitter.

Observability: pacing changes are reported through the caller-supplied
``log`` callable — one line per change, aligned with the existing
``worker slots`` log style, so the currently effective wait value is
visible in worker logs without per-claim spam.

The clock is injectable at the call site (the loop passes
``time.monotonic`` deltas), so the sequence is deterministically
testable (same pattern as ``worker/claim_backoff.py``).
"""

from __future__ import annotations

from collections.abc import Callable

# Lower bound of the success-path wait (seconds). Hard floor: a claim is
# a write transaction (advisory locks + promote writes) — unthrottled
# re-entry amplifies intra-transaction contention (#437 precedent).
CLAIM_PACING_FLOOR_SECONDS = 0.01
# Upper bound of the success-path wait. Above this a "short adaptive"
# pacing would degrade back toward the fixed-interval ceiling of #448.
CLAIM_PACING_CEIL_SECONDS = 0.1
# Wait target as a fraction of the last claim round-trip: half — pacing
# at the transaction's own speed buys nothing, and the sub-1 ratio
# de-correlates pacing from round-trip jitter.
CLAIM_PACING_TARGET_RATIO = 0.5

# Context: the fixed wait this module replaced (issue #448 measurement).
LEGACY_CLAIM_PACING_SECONDS = 0.2


def clamp_claim_pacing(round_trip: float) -> float:
    """Map one claim round-trip (seconds) to the pacing band.

    The mapping is a pure function of the round-trip: half the
    round-trip, clamped into ``[floor, ceil]``. Negative inputs clamp to
    the floor (a monotonic clock can formally return equal stamps).
    """
    target = max(round_trip, 0.0) * CLAIM_PACING_TARGET_RATIO
    return min(max(target, CLAIM_PACING_FLOOR_SECONDS), CLAIM_PACING_CEIL_SECONDS)


class ClaimPacing:
    """Success-path pacing for the claim loop: ``next_wait(round_trip)``
    after each pass that claimed at least one execution, ``reset()``
    when the next pass claims nothing, or ``wait_after_pass(...)`` to
    get the loop's post-claim wait for both non-error paths in one call.

    This pacing owns **only** the success path; the two adjacent paths
    keep their existing semantics — empty queue (claim None) → the
    caller still waits ``poll_interval``; claim errors → the caller
    still drives ``ClaimBackoffSequence``.

    ``log`` (optional) receives one line per *change* of the effective
    wait, judged at display precision (whole ms): round-trip jitter
    inside one displayed value costs no log volume.
    """

    def __init__(
        self,
        *,
        floor_seconds: float = CLAIM_PACING_FLOOR_SECONDS,
        ceil_seconds: float = CLAIM_PACING_CEIL_SECONDS,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self._floor_seconds = floor_seconds
        self._ceil_seconds = ceil_seconds
        self._log = log
        self._current = floor_seconds
        self._opened = False

    @property
    def current_wait(self) -> float:
        """The pacing in effect right now (floor until first claim)."""
        return self._current

    def next_wait(self, round_trip: float | None = None) -> float:
        """Return the wait after a successful claim pass.

        ``round_trip`` may be omitted when the caller cannot time the
        pass — the pacing then stays at its current value (in practice
        the band floor), i.e. claiming keeps the short-wait mode on.
        """
        if round_trip is None:
            return self._current
        target = self._clamp(round_trip)
        self._set(target)
        return target

    def wait_after_pass(self, claimed: bool, round_trip: float, empty_wait: float) -> float:
        """The loop's post-claim wait, both non-error paths in one call.

        Success adapts (``next_wait``); an empty pass resets the pacing
        and returns ``empty_wait`` (the loop's ``poll_interval``). The
        error path never reaches here: the loop's except arm drives
        ``ClaimBackoffSequence`` and leaves pacing untouched.
        """
        if not claimed:
            self.reset()
            return empty_wait
        return self.next_wait(round_trip)

    def reset(self) -> None:
        """The next pass claimed nothing: return to the band floor.

        The empty-queue path itself still waits ``poll_interval`` (the
        caller's decision); this reset only re-arms the success pacing
        so the first claim after an idle gap starts from the floor and
        emits the pacing-change log line again.
        """
        self._set(self._floor_seconds)

    def _clamp(self, round_trip: float) -> float:
        """Half the round-trip clamped into this instance's band."""
        target = max(round_trip, 0.0) * CLAIM_PACING_TARGET_RATIO
        return min(max(target, self._floor_seconds), self._ceil_seconds)

    def _set(self, wait: float) -> None:
        # 判变按显示精度（ms）量化：RTT 恒抖使 wait 每轮微变，精确相等
        # 判变会让成功热路径逐轮打日志（同步 print(flush=True) 的 I/O 税，
        # 正加在刚提速的循环上）；同显示值不重记，跨显示边界才记。
        # 内部值随之停在最后一次记日志的原始值上（与显示值等价）。
        if self._opened and round(wait * 1000) == round(self._current * 1000):
            return
        self._current = wait
        self._opened = True
        if self._log is not None:
            self._log(f"worker claim pacing {wait * 1000:.0f}ms")
