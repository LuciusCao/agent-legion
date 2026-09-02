"""In-process runtime-profile counters for the execution pipeline (#359 L1).

Six pipeline stages share one module-level registry: intake, pass, enqueue,
claim, execute, result. The hot paths (worker claim HTTP handler, result
commit, enqueue pool submit, workflow-worker pass loop, run intake) bump
plain integer/float attributes under a single ``Lock``-free discipline:

- counters are only ever incremented (or maximized). A plain ``+=`` under
  the GIL is NOT atomic: two interleaved LOAD/ADD/STORE pairs can lose one
  increment, and a maximized field can regress to a smaller concurrent
  value. Both losses only ever UNDERCOUNT a gauge (never fabricate load),
  which is acceptable for triage heuristics — write that down rather than
  claim safety. ``snapshot_and_reset`` holds the lock for the copy+reset,
  but writers do not take it: increments landing between the dict copy and
  the reset are permanently dropped (lost for both buckets) — again a
  bounded undercount at microsecond window × pipeline rates;
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

    ``enqueue_pools`` holds the live AgentEnqueuePool instances the workflow
    worker registers at startup — BOTH pools (agent bundling on
    agent_dispatch, code bundling on code_dispatch; independent-review
    P1 on #367 caught the single-registration variant measuring only the
    code pool). Typed as Any to keep this module free of agent_broker
    imports; write-once at startup, read-only afterwards.
    """

    enqueue_pools: list[Any] = []

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

    def note_execution_done(self, count: int = 1) -> None:
        self.counters.execute_done += count

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
        """Pure wall-time measurement handed to the note_* call (Codex P2 on
        #367): stop() only RETURNS the elapsed seconds — the note_* method
        owns every accumulation. An earlier variant also accumulated into
        the total here, double-counting every claim/result latency."""

        __slots__ = ("_start",)

        def __init__(self) -> None:
            self._start = time.monotonic()

        def stop(self) -> float:
            return time.monotonic() - self._start

    def claim_timer(self) -> _Timed:
        return RuntimeProfile._Timed()

    def result_timer(self) -> _Timed:
        return RuntimeProfile._Timed()


# Module-level singleton; the app wiring replaces it wholesale in tests.
profile = RuntimeProfile()
