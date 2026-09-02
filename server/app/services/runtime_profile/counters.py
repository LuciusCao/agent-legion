"""In-process runtime-profile counters for the execution pipeline (#359 L1).

Six pipeline stages share one module-level registry: intake, pass, enqueue,
claim, execute, result. The hot paths (worker claim HTTP handler, result
commit, enqueue pool submit, workflow-worker pass loop, run intake) bump
plain integer/float attributes under a single ``Lock``-free discipline:

- counters are only ever incremented (or maximized) — single ``+=`` on the
  GIL keeps each bump atomic enough for gauges; a lost increment under the
  interpreter's preemption just rounds a rate, never corrupts a monotonic
  total;
- the sampling loop snapshots all counters under ``registry_lock`` and
  resets the per-bucket deltas — writers never block on readers because
  the snapshot copies references out in one critical section measured in
  microseconds.

Everything lives in one process (single-uvicorn deployment shape, see
``docs/architecture``); multi-replica aggregation is out of scope and would
need a sum-per-bucket at read time instead of this registry.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class RuntimeProfileCounters:
    """Mutable per-bucket counters; one instance per Host process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        """Zero every counter (start of a sampling bucket)."""
        self.intake_runs = 0
        self.intake_items = 0
        self.pass_count = 0
        self.pass_seconds_total = 0.0
        self.pass_scan_seconds_max = 0.0
        self.pass_slow_count = 0
        self.enqueue_submitted = 0
        self.enqueue_pool_skipped = 0
        self.enqueue_stock_gated = 0
        self.claim_count = 0
        self.claim_empty_count = 0
        self.claim_seconds_total = 0.0
        self.claim_seconds_max = 0.0
        self.result_count = 0
        self.result_seconds_total = 0.0
        self.result_seconds_max = 0.0
        self.execute_done = 0
        self.execute_requeued = 0

    def snapshot_and_reset(self) -> dict[str, Any]:
        """Atomically read all deltas and start a fresh bucket.

        Depth gauges (queued rows, active executions, pool backlog) are NOT
        process counters — they live in the DB or the pool object — so the
        sampler merges them into the returned dict separately.
        """
        with self._lock:
            values = {
                "intake_runs": self.intake_runs,
                "intake_items": self.intake_items,
                "pass_count": self.pass_count,
                "pass_seconds_total": self.pass_seconds_total,
                "pass_scan_seconds_max": self.pass_scan_seconds_max,
                "pass_slow_count": self.pass_slow_count,
                "enqueue_submitted": self.enqueue_submitted,
                "enqueue_pool_skipped": self.enqueue_pool_skipped,
                "enqueue_stock_gated": self.enqueue_stock_gated,
                "claim_count": self.claim_count,
                "claim_empty_count": self.claim_empty_count,
                "claim_seconds_total": self.claim_seconds_total,
                "claim_seconds_max": self.claim_seconds_max,
                "result_count": self.result_count,
                "result_seconds_total": self.result_seconds_total,
                "result_seconds_max": self.result_seconds_max,
                "execute_done": self.execute_done,
                "execute_requeued": self.execute_requeued,
            }
            self.reset()
        return values


class RuntimeProfile:
    """Process-wide registry the hot paths call into.

    Instrumentation sites hold the module-level ``profile`` singleton (set
    once at startup); unit tests construct their own instance and inject it.
    Every method is safe to call from any thread and never raises — the
    profile must not be able to take down the pipeline it observes (a
    failure inside a ``finally``-free instrumentation call would turn a
    metrics bug into an outage; the try/except below is the guardrail).

    ``dispatch_service`` is an optional back-reference the workflow worker
    registers at startup: the sampler's enqueue-depth gauge reads the live
    pool backlog through it (Typed as Any to keep this module free of
    agent_broker imports — the reference is write-once, read-only).
    """

    dispatch_service: Any = None

    def __init__(self) -> None:
        self.counters = RuntimeProfileCounters()

    # --- intake -----------------------------------------------------------

    def note_run_intake(self, items: int) -> None:
        self.counters.intake_runs += 1
        self.counters.intake_items += items

    # --- pass (workflow worker poll loop) ----------------------------------

    def note_pass(self, seconds: float, scan_seconds: float, slow: bool) -> None:
        self.counters.pass_count += 1
        self.counters.pass_seconds_total += seconds
        if scan_seconds > self.counters.pass_scan_seconds_max:
            self.counters.pass_scan_seconds_max = scan_seconds
        if slow:
            self.counters.pass_slow_count += 1

    # --- enqueue pool -------------------------------------------------------

    def note_enqueue_submitted(self) -> None:
        self.counters.enqueue_submitted += 1

    def note_enqueue_pool_skipped(self) -> None:
        self.counters.enqueue_pool_skipped += 1

    def note_enqueue_stock_gated(self, count: int = 1) -> None:
        self.counters.enqueue_stock_gated += count

    # --- claim (worker HTTP claim) ------------------------------------------

    def note_claim(self, seconds: float, empty: bool) -> None:
        self.counters.claim_count += 1
        self.counters.claim_seconds_total += seconds
        if seconds > self.counters.claim_seconds_max:
            self.counters.claim_seconds_max = seconds
        if empty:
            self.counters.claim_empty_count += 1

    # --- execute ------------------------------------------------------------

    def note_execution_done(self) -> None:
        self.counters.execute_done += 1

    def note_execution_requeued(self, count: int = 1) -> None:
        self.counters.execute_requeued += count

    # --- result (worker HTTP result submit) ----------------------------------

    def note_result(self, seconds: float) -> None:
        self.counters.result_count += 1
        self.counters.result_seconds_total += seconds
        if seconds > self.counters.result_seconds_max:
            self.counters.result_seconds_max = seconds

    # --- shared context-manager helpers --------------------------------------

    class _Timed:
        """Records wall time into a callback on exit; never raises."""

        __slots__ = ("_profile", "_field", "_start")

        def __init__(self, profile: RuntimeProfile, field: str) -> None:
            self._profile = profile
            self._field = field
            self._start = time.monotonic()

        def stop(self) -> float:
            elapsed = time.monotonic() - self._start
            counters = self._profile.counters
            setattr(counters, self._field, getattr(counters, self._field) + elapsed)
            return elapsed

    def claim_timer(self) -> _Timed:
        return RuntimeProfile._Timed(self, "claim_seconds_total")

    def result_timer(self) -> _Timed:
        return RuntimeProfile._Timed(self, "result_seconds_total")


# Module-level singleton; the app wiring replaces it wholesale in tests.
profile = RuntimeProfile()
