"""Load-average claim backpressure (#566 phase 3).

A saturated machine that keeps claiming at full budget feeds the spiral the
rest of #566 dismantles: more work → less GIL for heartbeats/uploads → more
lease churn. The claim budget now decays with the 1-minute load average:
at or below the core count there is no decay; from 1× to 3× cores the
factor scales linearly down to a 0.25 floor (never zero — a fully clamped
worker could not recover its own drain-measure cycle, and a shared/CI box
under unrelated load must not stall the worker's claims entirely), and the
application rounds UP so a positive budget always keeps at least one slot.
Sampling is ``os.getloadavg`` cached for a few seconds — cheap by
construction, and platforms without getloadavg (Windows) pass load through
as unknown → no decay.

The same module owns the capacity sanity warning: a configured
max_concurrency far beyond the machine (default 4× cores) gets one startup
WARNING — over-declared capacity is the quiet version of the same bug.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable

LOAD_SAMPLE_CACHE_SECONDS = 5.0
# load1 / cores at which the decay factor reaches its floor; between 1× and
# this the decay is linear.
LOAD_FLOOR_RATIO = 3.0
# Never shed below this factor: a positive budget always keeps ceil(value ×
# factor) ≥ 1 slot, so refill never fully stalls on a shared/loaded box.
LOAD_FLOOR_FACTOR = 0.25
# max_concurrency beyond cores × this multiplier is flagged at startup.
CONCURRENCY_WARN_MULTIPLIER = 4


def load_budget_factor(load1: float, cores: int) -> float:
    """1.0 while load1 ≤ cores, linear to LOAD_FLOOR_FACTOR at 3× cores."""
    if cores <= 0:
        return 1.0
    ratio = load1 / cores
    if ratio <= 1.0:
        return 1.0
    span = LOAD_FLOOR_RATIO - 1.0
    return max(LOAD_FLOOR_FACTOR, 1.0 - (1.0 - LOAD_FLOOR_FACTOR) * (ratio - 1.0) / span)


def shed_value(value: int, factor: float) -> int:
    """A positive budget never sheds to zero (ceil) — refill slows, not stalls."""
    if value <= 0:
        return value
    return max(1, math.ceil(value * factor))


def concurrency_capacity_warning(max_concurrency: int, cores: int) -> str | None:
    """One-line startup warning when the declared capacity far exceeds the
    machine; None when the declaration is sane or cores are undetectable."""
    if cores <= 0 or max_concurrency <= cores * CONCURRENCY_WARN_MULTIPLIER:
        return None
    return (
        f"警告：max_concurrency={max_concurrency} 明显超出机器承载"
        f"（{cores} 核 ×{CONCURRENCY_WARN_MULTIPLIER}）——过载时租约心跳/上传会被拖垮，"
        "建议按机器实际容量下调"
    )


class LoadSampler:
    """os.getloadavg with a short cache; None = platform cannot tell."""

    def __init__(
        self,
        cache_seconds: float = LOAD_SAMPLE_CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        probe: Callable[[], tuple[float, float, float]] = os.getloadavg,
    ) -> None:
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._probe = probe
        self._cached: tuple[float, int] | None = None
        self._sampled_at = float("-inf")

    def sample(self) -> tuple[float, int] | None:
        now = self._clock()
        if self._cached is not None and now - self._sampled_at < self._cache_seconds:
            return self._cached
        try:
            load1 = self._probe()[0]
        except OSError:
            return None
        cores = os.cpu_count() or 1
        self._cached = (load1, cores)
        self._sampled_at = now
        return self._cached


class LoadShedder:
    """Apply the load factor to each pass's claim budget, logging transitions."""

    def __init__(
        self,
        max_concurrency: int,
        *,
        log: Callable[[str], None],
        sampler: LoadSampler | None = None,
    ) -> None:
        self._sampler = sampler or LoadSampler()
        self._log = log
        self._shedding = False
        if warning := concurrency_capacity_warning(max_concurrency, os.cpu_count() or 0):
            log(warning)

    def shed(self, budget: dict[str, int]) -> dict[str, int]:
        """Decay one pass's budget by the current load factor (0.0–1.0)."""
        sampled = self._sampler.sample()
        if sampled is None:
            return budget
        load1, cores = sampled
        factor = load_budget_factor(load1, cores)
        if factor >= 1.0:
            if self._shedding:
                self._shedding = False
                self._log(f"负载回落（load1={load1:.1f}/{cores} 核），claim 预算恢复正常")
            return budget
        if not self._shedding:
            self._shedding = True
            self._log(f"负载过高（load1={load1:.1f}/{cores} 核），claim 预算按 ×{factor:.2f} 衰减")
        return {kind: shed_value(value, factor) for kind, value in budget.items()}
