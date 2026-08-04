"""Metric dataclass and percentile helpers for the stress simulator."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StressMetrics:
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    agents: int = 0
    jobs_target: int = 0
    jobs_created: int = 0
    events_recorded: int = 0
    raw_events_per_second: float = 0.0
    sse_messages_received: int = 0
    sse_messages_per_second: float = 0.0
    patch_batch_sizes: list[int] = field(default_factory=list)
    flush_latencies_ms: list[float] = field(default_factory=list)
    stats_query_latencies_ms: list[float] = field(default_factory=list)
    memory_high_water_mb: float = 0.0
    resync_count: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        sizes = self.patch_batch_sizes
        latencies = self.flush_latencies_ms
        data["patch_batch_size_p50"] = _percentile(sizes, 0.5) if sizes else 0
        data["patch_batch_size_p95"] = _percentile(sizes, 0.95) if sizes else 0
        data["patch_batch_size_p99"] = _percentile(sizes, 0.99) if sizes else 0
        data["flush_latency_p50_ms"] = _percentile(latencies, 0.5) if latencies else 0
        data["flush_latency_p95_ms"] = _percentile(latencies, 0.95) if latencies else 0
        data["flush_latency_p99_ms"] = _percentile(latencies, 0.99) if latencies else 0
        return data


def _percentile(values: list[int] | list[float], q: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    idx = max(0, min(len(sorted_values) - 1, int(len(sorted_values) * q)))
    return float(sorted_values[idx])
