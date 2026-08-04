"""Deterministic GATE_SHARD=i/n hash sharding for pytest (CI postgres tier).

Loaded via ``-p scripts.pytest_gate_shard`` (scripts/check-quick-backend.sh
adds it when GATE_SHARD is set). After collection, each item is kept only
when ``md5(nodeid) % n == i - 1``, so the shards partition the collected set
deterministically across runs and machines. Filtering happens at collection
time on the xdist controller, so workers simply receive their share. Without
GATE_SHARD the plugin registers nothing and behavior is unchanged.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import pytest

SHARD_ENV_VAR = "GATE_SHARD"


def parse_shard_spec(value: str) -> tuple[int, int]:
    """Parse ``i/n`` into (index, count) with 1 <= index <= count."""
    parts = value.strip().split("/")
    if len(parts) != 2:
        raise ValueError(f"{SHARD_ENV_VAR} must be i/n (e.g. 1/2), got {value!r}")
    try:
        index, count = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"{SHARD_ENV_VAR} must be i/n (e.g. 1/2), got {value!r}") from None
    if count < 1 or not 1 <= index <= count:
        raise ValueError(f"{SHARD_ENV_VAR} requires 1 <= i <= n, got {value!r}")
    return index, count


def shard_index(nodeid: str, count: int) -> int:
    """Return the 0-based shard a nodeid belongs to (stable across runs)."""
    digest = hashlib.md5(nodeid.encode("utf-8"))  # deterministic sharding, not security
    return int(digest.hexdigest(), 16) % count


class GateShardFilter:
    """pytest hook wrapper that deselects items outside the configured shard."""

    def __init__(self, index: int, count: int) -> None:
        self._keep = index - 1
        self._count = count

    def pytest_collection_modifyitems(self, session: Any, config: Any, items: list) -> None:
        del session
        deselected = [
            item for item in items if shard_index(str(item.nodeid), self._count) != self._keep
        ]
        if deselected:
            config.hook.pytest_deselected(items=deselected)
        items[:] = [
            item for item in items if shard_index(str(item.nodeid), self._count) == self._keep
        ]


def pytest_configure(config: Any) -> None:
    value = os.environ.get(SHARD_ENV_VAR, "").strip()
    if not value:
        return
    try:
        index, count = parse_shard_spec(value)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    config.pluginmanager.register(GateShardFilter(index, count), name="gate-shard-filter")
