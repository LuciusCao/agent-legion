"""Unit tests for load-average claim backpressure (#566 phase 3)."""

from __future__ import annotations

import pytest

from worker.load_shedding import (
    LoadSampler,
    LoadShedder,
    concurrency_capacity_warning,
    load_budget_factor,
    shed_value,
)

pytestmark = pytest.mark.no_db


def test_load_budget_factor_curve() -> None:
    # At or below the core count: no decay.
    assert load_budget_factor(0.0, 8) == 1.0
    assert load_budget_factor(8.0, 8) == 1.0
    # Linear decay from 1× cores down to the floor at 3× cores.
    assert load_budget_factor(12.0, 8) == pytest.approx(0.8125)
    assert load_budget_factor(16.0, 8) == pytest.approx(0.625)
    assert load_budget_factor(24.0, 8) == pytest.approx(0.25)
    # The floor never reaches zero: refill slows but never stalls.
    assert load_budget_factor(32.0, 8) == pytest.approx(0.25)
    # Undetectable cores never decay.
    assert load_budget_factor(99.0, 0) == 1.0


def test_shed_value_keeps_one_slot() -> None:
    assert shed_value(0, 0.25) == 0
    assert shed_value(1, 0.25) == 1  # ceil: a positive budget never zeroes
    assert shed_value(6, 0.8125) == 5
    assert shed_value(32, 0.25) == 8


def test_sampler_caches_and_tolerates_missing_loadavg() -> None:
    probes: list[float] = []
    clock = [100.0]

    def probe() -> tuple[float, float, float]:
        probes.append(clock[0])
        return (4.0, 0.0, 0.0)

    sampler = LoadSampler(cache_seconds=5.0, clock=lambda: clock[0], probe=probe)
    first = sampler.sample()
    clock[0] += 1.0
    assert sampler.sample() == first  # cached within the window
    assert len(probes) == 1
    clock[0] += 5.0
    assert sampler.sample() == first
    assert len(probes) == 2  # cache expired → re-probed

    broken = LoadSampler(clock=lambda: clock[0], probe=lambda: (_ for _ in ()).throw(OSError()))
    assert broken.sample() is None


def test_shedder_decays_budget_and_logs_transitions(monkeypatch: pytest.MonkeyPatch) -> None:
    logs: list[str] = []
    load = [4.0]
    sampler = LoadSampler(probe=lambda: (load[0], 0.0, 0.0))
    shedder = LoadShedder(4, log=logs.append, sampler=sampler)
    monkeypatch.setattr("worker.load_shedding.os.cpu_count", lambda: 8)
    budget = {"agent": 6, "code": 2}
    # load ≤ cores: passthrough (same object), no log.
    assert shedder.shed(budget) == budget
    assert logs == []
    # 1.5× cores: factor 0.8125, ceil application.
    load[0] = 12.0
    sampler._cached = None
    assert shedder.shed(budget) == {"agent": 5, "code": 2}
    assert len(logs) == 1 and "衰减" in logs[0]
    # Still shedding: no repeated log.
    assert shedder.shed(budget) == {"agent": 5, "code": 2}
    assert len(logs) == 1
    # 3× cores: floor factor 0.25 (still shedding — no repeated log).
    load[0] = 24.0
    sampler._cached = None
    assert shedder.shed(budget) == {"agent": 2, "code": 1}
    assert len(logs) == 1
    # Recovery: passthrough + recovery log.
    load[0] = 4.0
    sampler._cached = None
    assert shedder.shed(budget) == budget
    assert len(logs) == 2 and "恢复正常" in logs[-1]


def test_shedder_passes_through_when_load_unknown() -> None:
    sampler = LoadSampler(probe=lambda: (_ for _ in ()).throw(OSError()))
    shedder = LoadShedder(4, log=lambda line: None, sampler=sampler)
    budget = {"agent": 3, "code": 1}
    assert shedder.shed(budget) == budget


def test_capacity_warning_threshold() -> None:
    assert concurrency_capacity_warning(8, 8) is None
    assert concurrency_capacity_warning(32, 8) is None
    warning = concurrency_capacity_warning(33, 8)
    assert warning is not None and "max_concurrency=33" in warning
    assert concurrency_capacity_warning(100, 0) is None  # cores undetectable
