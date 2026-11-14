"""Duration-weighted whole-file sharding for the CI PostgreSQL tier."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import pytest

DURATIONS_ENV_VAR = "GATE_SHARD_DURATIONS"
RESERVED_ENV_VAR = "GATE_SHARD_RESERVED_SECONDS"
FileTimings = dict[str, tuple[float, int]]


def parse_reserved_seconds(value: str, count: int) -> tuple[float, ...]:
    if not value.strip():
        return (0.0,) * count
    parts = value.split(",")
    if len(parts) != count:
        raise ValueError(f"{RESERVED_ENV_VAR} must contain exactly {count} comma-separated values")
    try:
        seconds = tuple(float(part) for part in parts)
    except ValueError:
        raise ValueError(f"{RESERVED_ENV_VAR} values must be numbers") from None
    if any(not math.isfinite(part) or part < 0 for part in seconds):
        raise ValueError(f"{RESERVED_ENV_VAR} values must be finite and non-negative")
    return seconds


def load_duration_manifest(path: Path) -> FileTimings:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError(f"{path}: unsupported duration manifest")
    raw_files = payload.get("files")
    if not isinstance(raw_files, dict) or not raw_files:
        raise ValueError(f"{path}: files must be a non-empty object")
    timings: FileTimings = {}
    for filename, entry in raw_files.items():
        if (
            not isinstance(filename, str)
            or not filename.startswith("tests/")
            or not filename.endswith(".py")
            or not isinstance(entry, dict)
        ):
            raise ValueError(f"{path}: invalid file timing entry {filename!r}")
        seconds = entry.get("seconds")
        tests = entry.get("tests")
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds < 0
            or isinstance(tests, bool)
            or not isinstance(tests, int)
            or tests < 1
        ):
            raise ValueError(f"{path}: invalid timing values for {filename}")
        timings[filename] = (float(seconds), tests)
    if not any(seconds > 0 for seconds, _ in timings.values()):
        raise ValueError(f"{path}: manifest has no positive timing data")
    return timings


def weighted_shard_nodeids(
    nodeids: list[str],
    count: int,
    timings: FileTimings,
    reserved_seconds: tuple[float, ...] = (),
) -> tuple[list[set[str]], tuple[float, ...]]:
    if reserved_seconds and len(reserved_seconds) != count:
        raise ValueError("reserved_seconds must match the shard count")
    groups: dict[str, list[str]] = {}
    for nodeid in nodeids:
        groups.setdefault(nodeid.split("::", 1)[0], []).append(nodeid)
    default_per_test = sum(seconds for seconds, _ in timings.values()) / sum(
        tests for _, tests in timings.values()
    )
    loads = list(reserved_seconds or (0.0,) * count)
    shards: list[set[str]] = [set() for _ in range(count)]
    estimates = {}
    for filename, group in groups.items():
        if filename in timings:
            seconds, previous_count = timings[filename]
            estimates[filename] = seconds / previous_count * len(group)
        else:
            estimates[filename] = default_per_test * len(group)
    for filename in sorted(groups, key=lambda name: (-estimates[name], name)):
        shard = min(range(count), key=lambda index: (loads[index], index))
        shards[shard].update(groups[filename])
        loads[shard] += estimates[filename]
    return shards, tuple(loads)


class DurationGateShardFilter:
    """Deselect items outside the configured duration-weighted shard."""

    def __init__(
        self, index: int, count: int, timings: FileTimings, reserved_seconds: tuple[float, ...]
    ) -> None:
        self._keep = index - 1
        self._count = count
        self._timings = timings
        self._reserved_seconds = reserved_seconds
        self.estimated_loads: tuple[float, ...] = ()

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, session: Any, config: Any, items: list) -> None:
        del session
        shards, self.estimated_loads = weighted_shard_nodeids(
            [str(item.nodeid) for item in items],
            self._count,
            self._timings,
            self._reserved_seconds,
        )
        keep = shards[self._keep]
        deselected = [item for item in items if item.nodeid not in keep]
        if deselected:
            config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if item.nodeid in keep]

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if self.estimated_loads:
            loads = ", ".join(f"{seconds:.1f}" for seconds in self.estimated_loads)
            terminalreporter.write_line(f"GATE_SHARD estimated loads (seconds): {loads}")


def configure_duration_shard(config: Any, index: int, count: int) -> bool:
    duration_path = os.environ.get(DURATIONS_ENV_VAR, "").strip()
    reserved_value = os.environ.get(RESERVED_ENV_VAR, "")
    if not duration_path:
        if reserved_value.strip():
            raise pytest.UsageError(f"{RESERVED_ENV_VAR} requires {DURATIONS_ENV_VAR}")
        return False
    try:
        timings = load_duration_manifest(Path(duration_path))
        reserved = parse_reserved_seconds(reserved_value, count)
    except (OSError, ValueError) as exc:
        raise pytest.UsageError(str(exc)) from exc
    config.pluginmanager.register(
        DurationGateShardFilter(index, count, timings, reserved), name="gate-shard-filter"
    )
    return True
